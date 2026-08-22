import ast
import hashlib
import logging.config
import os
import os.path
import shutil
import subprocess
import tempfile
from typing import Set, Dict, Iterable

from .XIO import XIO
from .helper import get_cdir, get_logger, join_path_abs  # pylint: disable=E0402
from .neonconfig import NeonConfig
from .protocol import Protocol, Domain

logger = get_logger("Program Handler")


class ProgramHandler:
    """
    The program handlers responsibility is to compile programs and to upload the compiled programs to the
    executable environments (XIO's).

    It also implements a few quality-of-life improvements, such as a system that only recompiles a program if it
    has changed and thus prevents unnecessary re-compilations or NEON's auto-timer feature which can automatically
    assign timer values to named variables.
    """
    config: NeonConfig
    path_to_mpspdz: str
    cdir: str

    def __init__(self, config: NeonConfig):
        """Setup the local MPSPDZ directory.
        """
        self.config = config
        self.config.local_mpspdz_path = config.local_mpspdz_path
        self.cdir = get_cdir()

    def program_dir(self, *parts: str) -> str:
        """Directory holding the .mpc sources.

        Upstream NEON resolves this as ``<package>/../Programs``. This copy is
        vendored one level deeper, and the MPC program is not vendor code, so
        the sources live at ``<repository root>/mpc`` instead.
        """
        return join_path_abs(self.cdir, '..', '..', 'mpc', *parts)

    def compile_program_if_necessary(self, program: str, protocol: Protocol, compile_debug, compile_looping=False) -> str:
        """Compiles the program locally if necessary and returns the program's hash."""
        # Some are binary circuits only and need different compilation
        hash = self.get_hash(program, protocol).hex()

        if not self.check_program_update(hash, compile_debug):
            # Delete the temporary file
            self.delete_temporary_program(program)
            return hash

        dependencies = self.get_dependencies_for_program(program)
        self.compile_program_locally(dependencies, program, hash, protocol, compile_debug, compile_looping)
        return hash

    def compile_program_locally(self, dependencies: Set[str], program: str, program_hash: str, protocol: Protocol,
                                compile_debug, compile_looping=False):
        """Compiles the program locally."""
        try:
            shutil.copyfile(self.program_dir(program + '.mpc'),
                            join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Source', program_hash + '.mpc'))
            # Delete the temporary file
            self.delete_temporary_program(program)
            if len(dependencies) > 0:
                os.makedirs(join_path_abs(self.config.local_mpspdz_path, 'Dependencies'), exist_ok=True)
            for dependency in dependencies:
                shutil.copyfile(self.program_dir('Dependencies', dependency),
                                join_path_abs(self.config.local_mpspdz_path, 'Dependencies', dependency))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location", err, program_hash)
        compiler_file = self.config.local_mpspdz_path + "/compile.py"
        try:
            args = [compiler_file]
            if protocol.domain == Domain.PRIME and self.config.prime is not None:
                args.append("-P " + str(self.config.prime))
            elif protocol.domain == Domain.PRIME:
                args.append("-F 127")
            elif protocol.domain == Domain.BINARY:
                args.append("-B 64")
            elif protocol.domain == Domain.RING:
                args.append("-R 64")
            else:
                raise NotImplementedError("Compiling for other domains not implemented")

            if compile_debug:
                # Extend is required for use of "/" in path name here
                args.extend(["-a", "Programs/asm"])

            if compile_looping:
                args.append("-l")
            args.append(program_hash)
            subprocess.run(args, check=True, cwd=self.config.local_mpspdz_path)
        except Exception as err:
            self.exit_after_error("Cannot compile program", err, program_hash)

    def get_tape_names(self, program_hash: str):
        schedule_file = join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch')
        with open(schedule_file, 'r') as f:
            # First two lines are parsed, although not realy needed
            num_threads = int(f.readline().strip())
            num_tapes = int(f.readline().strip())
            # The .rsplit(':',1)[0] is required as mp-spdz puts now :xxx at the end of each "filename" inside the schedulefile
            tape_names = [tape_name.rsplit(':', 1)[0] for tape_name in f.readline().strip().split(' ')]
            if len(tape_names) != num_tapes:
                raise Exception('Tape/Schedule format problem')
            return tape_names

    def check_program_update(self, program_hash: str, compile_debug: bool):
        """Check if the program needs to be updated"""
        if not os.path.isfile(
                join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Source', program_hash + '.mpc')):
            return True
        if not os.path.isfile(
                join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch')):
            return True

        try:
            tape_names = self.get_tape_names(program_hash)
            for tape_name in tape_names:
                if not os.path.isfile(
                        join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc')):
                    return True
            if compile_debug:
                for tape_name in tape_names:
                    if not os.path.isfile(join_path_abs(self.config.local_mpspdz_path, 'Programs', 'asm-' + tape_name)):
                        return True
        except:
            return True

    def upload_compiled_program(self, program_hash: str, config: NeonConfig, xio: XIO) -> None:
        try:
            xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch'),
                            join_path_abs(xio.remote_path_to_mpdspz, 'Programs', 'Schedules', program_hash + '.sch'))

            tape_names = self.get_tape_names(program_hash)
            for tape_name in tape_names:
                xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'),
                                join_path_abs(xio.remote_path_to_mpdspz, 'Programs', 'Bytecode', tape_name + '.bc'))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location.", err, program_hash)

    def determine_program_timers(self, program: str) -> (Dict[int, str], Dict[str, int]):
        result_timer_names = {}
        variable_name_to_display_name = {}
        autotimers = []
        used_timers = []

        # Go over the program and try to determine the used timers as well as specified timer names.
        with open(self.program_dir(program + '.mpc'), 'r') as f:
            for line in f.readlines():
                line = line.lstrip('\t ').rstrip('\n\r')

                if line.lower().startswith('#neon_timer'):
                    # Parse header information
                    parts = line.split(' ')
                    timer = parts[1]
                    name = ' '.join(parts[2:])

                    if timer.isnumeric():
                        # A timer number is given, we shall add it to the data structure.
                        timer = int(timer)
                        result_timer_names[timer] = name
                        used_timers.append(int(timer))
                    else:
                        # A long name for a variable is given, we shall store it for later use.
                        # Cut of the NEON_ if needed.
                        if timer.startswith('NEON_'):
                            timer = timer[5:]
                        variable_name_to_display_name[timer] = name

                elif "start_timer(" in line:
                    # Detect timer usage and autotimers.
                    parts = line.split("start_timer(")
                    parameter = parts[1][:parts[1].index(')')]

                    if parameter.isnumeric():
                        used_timers.append(int(parameter))
                    elif parameter.startswith('NEON_'):
                        autotimers.append(parameter[5:])

        # Determine the first clear timer to be assigned.
        auto_start = 1
        if len(used_timers) > 0:
            auto_start = max(used_timers) + 1

        # Assign timer id's to the auto-timers.
        substitutions = {}
        for (i, name) in enumerate(autotimers):
            timer_id = auto_start + i
            # We do it this way to ensure that only timers are substituted. Also fixes the case in which one timer's name
            # is a prefix of another's timers name.
            substitutions[f"start_timer(NEON_{name})"] = f"start_timer({timer_id})"
            substitutions[f"stop_timer(NEON_{name})"] = f"stop_timer({timer_id})"
            result_timer_names[timer_id] = variable_name_to_display_name[
                name] if name in variable_name_to_display_name.keys() else name

        return (result_timer_names, substitutions)

    def delete_hashes(self, program_hash: str) -> None:
        """In case of a failed compile this delete partial or corrupt files"""
        if not os.path.isfile(
                join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch')):
            return
        files = [join_path_abs('Programs', 'Source', program_hash + '.mpc'),
                 join_path_abs('Programs', 'Source', program_hash + '.sch')]
        tape_names = self.get_tape_names(program_hash)
        for tape_name in tape_names:
            files.append(join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'))
        for tape_name in tape_names:
            files.append(join_path_abs(config.local_mpspdz_path, 'Programs', 'asm-' + tape_name))
        for file in files:
            if os.path.isfile(self.config.local_mpspdz_path + file):
                os.remove(self.config.local_mpspdz_path + file)

    def get_hash(self, program: str, protocol: Protocol) -> bytes:
        """This function takes the content of the program and its dependencies and
        creates a hash from this. This hash is used to determine if a program with exactly
        these files has already been compiled and if so, one does not have to newly compile"""
        dependencies = self.get_dependencies_for_program(program)
        logger.info("Program has the following dependencies: {}".format(dependencies))

        with open(self.program_dir(program + '.mpc'), 'rb') as f:
            new_code = f.read()

        # Change hash depending on the domain (and possibly other parameters) that it needs
        if protocol:
            new_code += str(protocol.domain.name).encode('utf-8')

        for dependency in sorted(list(dependencies)):
            with open(self.program_dir('Dependencies', dependency), 'rb') as f:
                new_code += f.read()

        return hashlib.blake2s(new_code).digest()

    def get_dependencies_for_program(self, program: str) -> Set[str]:
        """
        Returns all python dependencies for the given program.
        This will likely not work for package like structures, subfolders or similar.
        All imports have to be prefixed by `Dependencies.`

        Parameters
        ----------
        program : str
            Name of the program

        Returns
        -------
        set<str>
            Set of all python dependency file names
        """

        dependencies = set()
        new_files = set()
        new_files.add("../" + program + '.mpc')
        next_round_new_files = set()

        dependendencies_folder = self.program_dir('Dependencies')

        def try_add(file):
            if os.path.isfile(join_path_abs(dependendencies_folder, file)):
                dependencies.add(file)
            # Cut away prefix and filter out all non custom dependencies
            path_parts = file.split(".")
            """if len(path_parts) < 3:
                logger.warning("Malformed import " + file + " (Missing prefix?)")
                # exit(1)
                return"""
            if path_parts[0] == "Dependencies":
                file = ".".join(path_parts[1:])
                if file not in dependencies:
                    dependencies.add(file)
                    next_round_new_files.add(file)

        while len(new_files) > 0:
            for new_file in new_files:
                # https://stackoverflow.com/questions/9008451/python-easy-way-to-read-all-import-statements-from-py-module
                try:
                    with open(join_path_abs(dependendencies_folder, new_file), 'r') as f:
                        root = ast.parse(f.read(), join_path_abs(dependendencies_folder, new_file))
                except Exception as e:
                    logger.critical("Could not open file " + join_path_abs(dependendencies_folder,
                                                                           new_file) + " or read its dependencies!")
                    logger.error("The error was :\n" + str(e))
                    exit(1)
                for node in ast.walk(root):
                    if isinstance(node, ast.Import):
                        for n in node.names:
                            try_add(n.name + ".py")
                    elif isinstance(node, ast.ImportFrom):
                        try_add(node.module + ".py")
            new_files = next_round_new_files
            next_round_new_files = set()

        return dependencies

    def perform_substitution(self, program: str, find_and_replace: Dict[str, str]) -> str:
        """Performs the substituions that were set with set_substitution, returns the filename of the resulting temporary program."""

        # create temp file
        file_handler_id, temp_file = tempfile.mkstemp(suffix=".mpc", dir=self.program_dir())
        # Manually close the file. Must be done, as otherwise the temporary file will be always open, and thus can cause the "too many open files" error.
        os.close(file_handler_id)
        # Find and replace
        with open(self.program_dir(program + '.mpc'), 'r') as f:
            filedata = f.read()
        for k, v in find_and_replace.items():
            filedata = filedata.replace(k, v)
        with open(temp_file, 'w') as f:
            f.write(filedata)
        # return the name of  the temporary file
        return temp_file.split('/')[-1].split('.')[0]

    def delete_temporary_program(self, temp_program: str):
        """delete the temporary program from the substitution after it has been compiled"""
        os.remove(self.program_dir(temp_program + '.mpc'))

    def exit_after_error(self, error, original_error, program_hash: str):
        """In case of an error this function gives feedback and deletes
        any corrupt files"""
        logger.exception(original_error)
        logger.critical(error)
        self.delete_hashes(program_hash)
        exit(1)
