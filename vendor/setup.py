#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from typing import List
import requests

from tqdm import tqdm, trange
from multiprocessing.pool import ThreadPool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ProgramFiles.helper import get_cdir, get_logger, get_path_to_mpspdz, generate_party_certificate, rehash_certificates, join_path_abs, NeonException
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.virtualnet import VirtualNetworkManager

logger = get_logger('Setup')


# region Utility function
def create_folder_if_not_exists(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)
# endregion


class SetupCommandLineInterface:
    def __init__(self):
        allowed_commands = ['install-mpspdz', 'prepare-distributed', 'clean-virtual']
        parser = argparse.ArgumentParser(
            description='Setup and management script for NEON.',
            usage=f'''./setup.py <command> [args]
            
Available commands: {', '.join(allowed_commands)}
            '''
        )
        parser.add_argument('command')
        args = parser.parse_args(sys.argv[1:2])
        command: str = args.command
        command_function_name = command.replace('-', '_')
        if command not in allowed_commands or not hasattr(self, command_function_name):
            logger.critical(f'Unrecognized command: {command}')
            parser.print_help()
            return

        getattr(self, command_function_name)(sys.argv[2:])

    def _download_mpspdz_release(self, version: str, installation_folder: str):
        cdir = get_cdir()

        # Fetch the version id of the latest version if necessary.
        if version == 'latest':
            version = requests.get("https://api.github.com/repos/data61/MP-SPDZ/releases/latest").json()["tag_name"][1:]

        logger.info(f"Installing MP-SPDZ version {version}")

        # Download the precompiled MP-SPDZ version.
        url = f"https://github.com/data61/MP-SPDZ/releases/download/v{version}/mp-spdz-{version}.tar.xz"
        local_filename = join_path_abs(cdir, '..', 'temp', 'MPzips', url.split('/')[-1])
        if not os.path.isfile(local_filename):  # Skip download if the latest version is already downloaded.
            logger.info(f"Downloading MP-SPDZ version: {version}")
            with requests.get(url, stream=True) as r:
                with open(local_filename, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)
        else:
            logger.info("MP-SPDZ already downloaded.")

        # Extract the downloaded ZIP into the installation folder.
        logger.info(f"Decompressing {local_filename} please be patient...")
        with tarfile.open(local_filename) as f:
            f.extractall(installation_folder)

    def _download_and_compile_mpspdz_from_git(self, installation_folder: str, protocols: List[str]):
        logger.info("Downloading MP-SPDZ version: git")
        subprocess.run("git clone https://github.com/data61/MP-SPDZ.git", shell=True, cwd=installation_folder)
        path_to_mpspdz = get_path_to_mpspdz()
        
        nr_cpu_cores = os.cpu_count()
        
        tools_to_compile = ["setup"]

        if not protocols:
            # Make all protocols
            tools_to_compile.append("")
        else:
            tools_to_compile.extend(protocols)
                
        for to_make in tools_to_compile:
            if to_make:
                logger.info(f"Building {to_make}. Please wait")
            else:
                logger.info(f"Building all protocols. Please wait")
            subprocess.run(f"make -j {nr_cpu_cores} {to_make}", shell=True, cwd=path_to_mpspdz, check=True)

    def install_mpspdz(self, args: List[str]):
        parser = argparse.ArgumentParser(description='Update the local MP-SPDZ version.')
        parser.add_argument('--version', type=str, default='latest', dest='version',
                            help='The MP-SPDZ-version to be installed.')
        parser.add_argument('--protocols', type=str, nargs='+', dest='protocols',
                            help='Requires "--version git", list of protocols to be compiled. If not specified, all are compiled')
        args = parser.parse_args(args)
        version = args.version

        logger.debug(f'Installing, args={args}')

        p = subprocess.Popen("command -v zstd", shell=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        if "zstd" not in out.decode():
            logger.critical("zstd is not installed. Please install it first.")
            return

        # Create missing directories and files (if any)
        logger.info("Creating missing directories.")
        folders = ["temp", "temp/MPzips", "temp/MP", "logs", "config", "Programs/Dependencies/"]
        cdir = join_path_abs(get_cdir(), '..')
        for folder in folders:
            create_folder_if_not_exists(join_path_abs(cdir, folder))
        open(join_path_abs(cdir, "config", "ip.conf"), 'a').close()

        # Remove prior MP-SPDZ installations.
        cdir = get_cdir()
        mp_spdz_installation_folder = join_path_abs(cdir, '..', 'temp', 'MP')
        shutil.rmtree(mp_spdz_installation_folder)
        create_folder_if_not_exists(mp_spdz_installation_folder)

        if version == 'git':
            self._download_and_compile_mpspdz_from_git(mp_spdz_installation_folder, args.protocols)
        else:
            self._download_mpspdz_release(version, mp_spdz_installation_folder)

        # Setup the new MP-SPDZ installation.
        path_to_mpspdz = get_path_to_mpspdz()
        if version != 'git':
            logger.info("Finalizing MP-SPDZ install.")
            subprocess.run('Scripts/tldr.sh', shell=True, cwd=path_to_mpspdz, stdout=subprocess.DEVNULL)
        create_folder_if_not_exists(join_path_abs(cdir, "..", "Programs", "Dependencies"))
        create_folder_if_not_exists(join_path_abs(path_to_mpspdz, "Persistence"))
        create_folder_if_not_exists(join_path_abs(path_to_mpspdz, "Player-Data"))
        create_folder_if_not_exists(join_path_abs(path_to_mpspdz, "Programs", "Schedules"))
        create_folder_if_not_exists(join_path_abs(path_to_mpspdz, "Programs", "Bytecode"))

    def prepare_distributed(self, args: List[str]):
        parser = argparse.ArgumentParser(description='Prepares remote machines to be used as MP-SPDZ clients for computations.')
        parser.add_argument('--sshkey', type=str, default=None, dest='ssh_key',
                            help='The SSH key to be used when connecting to the remote machines.')
        args = parser.parse_args(args)

        # Test if the installation has run before.
        cdir = join_path_abs(get_cdir(), '..')
        if not os.path.isdir(join_path_abs(cdir, 'temp')):
            logger.critical('You need to install MP-SPDZ locally first.')
            logger.critical('Please run "./setup.py install-mpspdz')
            return

        # Prepare the neon-config.
        config = NeonConfig.from_config_files()
        if args.ssh_key:
            config.ssh_key = args.ssh_key
        config.ensure_distribution_mode_compatibility(fail_hard=True)

        remote_machine_without_zstd = False
        for remote_machine in config.ip_list:
            command_template = 'ssh -i ' + config.ssh_key + ' -o "StrictHostKeyChecking no"' + ' root@' + \
                               remote_machine + ' "{}"'

            # Check if zstd is installed.
            p = subprocess.Popen(command_template.format("command -v zstd"), shell=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate()
            if "zstd" not in out.decode():
                # Install zstd if necessary.
                logger.critical(f"zstd not installed on remote machine {remote_machine}. Please install zstd first.")
                remote_machine_without_zstd = True
        if remote_machine_without_zstd:
            return


        path_to_mpspdz = get_path_to_mpspdz()
        # TODO check if this can be made threaded
        for cert_number in trange(len(config.ip_list), desc="Generating Certificates"):
            generate_party_certificate(path_to_mpspdz, cert_number)
        rehash_certificates(path_to_mpspdz)


        # Compress the data to be uploaded to the target machines.
        logger.info("Please wait, while tar archive is being created. This may take a while!")
        F = './temp/'
        Fzip = './temp.tar.zst'
        subprocess.run('tar -cf - ' + F + '/ | zstd -18 -T0 -f -o ' + Fzip, shell=True, cwd=cdir)

        def func_prepare_remote_machine(remote_machine):
            command_template = 'ssh -i ' + config.ssh_key + ' -o "StrictHostKeyChecking no"' + ' root@' + \
                               remote_machine + ' "{}"'

            # Delete old files
            subprocess.run(command_template.format(f"rm -rf {config.remote_path}"), shell=True)
            
            # Prepare upload destination.
            subprocess.run(command_template.format(f"mkdir -p {config.remote_path}"), shell=True)

            # Upload the data.
            subprocess.run(f'scp -i {config.ssh_key} {Fzip} root@{remote_machine}:{config.remote_path}', shell=True, cwd=cdir, stdout=subprocess.DEVNULL)

            # Decompress the uploaded data.
            subprocess.run(command_template.format(f"cd {config.remote_path} ; tar -I zstd -xf temp.tar.zst"), shell=True)
            
            # Delete tar archive
            subprocess.run(command_template.format(f"rm -rf {config.remote_path}/temp.tar.zst"), shell=True)

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_prepare_remote_machine, config.ip_list), total=len(config.ip_list), desc="Preparing remote hosts"):
                pass
        
        # Delete tar archive
        subprocess.run(f"rm {Fzip}", shell=True, cwd=cdir)

    def clean_virtual(self, args: List[str]):
        VirtualNetworkManager.clean_previous_neon_namespaces()
        logger.info("Removed previous virtual namespaces.")
        


if __name__ == '__main__':
    SetupCommandLineInterface()
