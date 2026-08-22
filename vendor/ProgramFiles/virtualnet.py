import math
import subprocess
import os
from typing import List
from tqdm import tqdm
from multiprocessing.pool import ThreadPool

from ProgramFiles.helper import join_path_abs, NeonException

"""This file offloads and abstracts any working with the virtual interfaces. All commands used 
are thoroughly documented and any parameters used for the functions like bas eips should be defined 
in this file or a config, to keep it out of the other NEON files. 
"""
import logging
import logging.config

logging.config.fileConfig(
    join_path_abs(os.path.dirname(__file__), '../config/logging.conf'),
    disable_existing_loggers=False)
# logger = logging.getLogger(__name__)
logger = logging.getLogger("Virtual Network")


class VirtualNetworkManager:
    """The virtual network manager is responsible for creating, expanding and destroying virtual networks.
    If possible, it will try to keep existing virtual networks to improve the performance of NEON."""

    __bridge_active: bool = False
    """Is the network's bridge active?"""
    __current_active_namespaces: int = 0
    """The number of currently existing namespaces."""

    def initial_setup(self):
        VirtualNetworkManager.clean_previous_neon_namespaces()
        self.__setup_bridge()

    def __setup_bridge(self):
        """Creates the initial bride and its ip necessary for the namespaces"""
        general = [
            # Create a bridge
            'ip link add name neon_bridge txqueuelen 10000 type bridge',

            # set bridge up
            'ip link set neon_bridge up',

            # Give bridge an IP
            'ip addr add 172.16.1.10/16 brd + dev neon_bridge'
        ]
        # Commands of intereset
        # sysctl -w net.bridge.bridge-nf-call-arptables=0
        # sysctl -w net.bridge.bridge-nf-call-iptables=0
        # sysctl -w net.bridge.bridge-nf-call-ip6tables=0
        # sysctl -w net.bridge.bridge-nf-call-ip6tables=0
        # sysctl -w net.ipv4.icmp_ratelimit=0

        try:
            VirtualNetworkManager._run_commands(general)
            self.__bridge_active = True
        except Exception as e:
            logger.error(e)
        finally:
            logger.debug("done")

    def setup_network(self, n_parties: int):
        """Set up's the network such that n_parties will be able to perform a computation in that network."""
        if not self.__bridge_active:
            self.__setup_bridge()

        namespaces_to_create = list(range(self.__current_active_namespaces, n_parties))
        
        def func_setup_network(i):
            ip = VirtualNetworkManager.get_ip(i)
            self._run_commands([
                # Create a new network namespace nsi
                f'ip netns add neon_ns{i}',

                # Create a veth pair to tunnel data into the bridge. vethi is the nsi side, br-vethi the bridge side
                f'ip link add neon_veth{i} type veth peer name neon_br_v{i}',

                # Move the interface vethi into the namespace nsi
                f'ip link set neon_veth{i} netns neon_ns{i}',

                # Give the interface an ip depending on the value i
                f'ip netns exec neon_ns{i} ip addr add {ip}/24 dev neon_veth{i}',

                # Set bridge interface side of tunnel up from default namespace
                f'ip link set neon_br_v{i} up',

                # Set the other side of tunnel up from nsi
                f'ip netns exec neon_ns{i} ip link set neon_veth{i} up',

                # Set bridge br1 as the master of our bridge side interface of the tunnel
                f'ip link set neon_br_v{i} master neon_bridge',

                # Start local host so party 0 can communicate with itself in neon
                f'ip netns exec neon_ns{i} ip link set dev lo up',
            ])

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_setup_network, namespaces_to_create), initial=self.__current_active_namespaces, total=len(namespaces_to_create)+self.__current_active_namespaces, desc='Setting up network', leave=None):
                pass


        # We want to verify that the network actually works and also ensure that the bridge's MAC address
        # database is filled. That's why let each host ping all other hosts.
        def func_check_network(party_index):
            for j in range(0, n_parties):
                if party_index == j:
                    continue

                self._run_commands([f'ip netns exec neon_ns{party_index} ping -c 1 {VirtualNetworkManager.get_ip(j)} > /dev/null'])

                if j < self.__current_active_namespaces:
                    self._run_commands([f'ip netns exec neon_ns{j} ping -c 1 {VirtualNetworkManager.get_ip(party_index)} > /dev/null'])

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_check_network, namespaces_to_create), initial=self.__current_active_namespaces, total=len(namespaces_to_create)+self.__current_active_namespaces, desc='Checking network', leave=None):
                pass

        self.__current_active_namespaces = max(self.__current_active_namespaces, n_parties)
        VirtualNetworkManager.check_and_set_neighbor_table_size(self.__current_active_namespaces)

    def tear_down_namespaces(self):
        """Destroys the virtual network completely."""
        VirtualNetworkManager.clean_previous_neon_namespaces()
        self.__bridge_active = False
        self.__current_active_namespaces = 0

    def __del__(self):
        VirtualNetworkManager.clean_previous_neon_namespaces()

    @staticmethod
    def get_ip(namespace: int) -> str:
        """Returns the IP address of the given client."""
        return f"172.16.{1 + namespace // 245}.{11 + namespace % 245}"

    @staticmethod
    def clean_previous_neon_namespaces():
        """Searches for NEON-related virtual networks and removes them."""

        # Find existing neon bridges
        tmp_process = subprocess.Popen('ip link list', shell=True, stdout=subprocess.PIPE)
        out, _ = tmp_process.communicate()
        bridges = []
        for line in out.decode().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                namespace = parts[1].rstrip(':')
                if not namespace.startswith('neon_'):
                    continue
                if '@' in namespace:
                    at_pos = namespace.index('@')
                    namespace = namespace[0:at_pos]
                bridges.append(namespace)

        logger.debug(f"Bridges left over from previous runs: {bridges}")

        tmp_process = subprocess.Popen('ip netns list', shell=True, stdout=subprocess.PIPE)
        out, _ = tmp_process.communicate()
        namespaces = []
        for line in out.decode().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                namespace = parts[0]
                if namespace.startswith('neon_'):
                    namespaces.append(namespace)

        logger.debug(f"Namespaces left over from previous runs: {namespaces}")

        commands = [f"ip link delete {bridge}" for bridge in bridges]
        commands += [f"ip netns delete {namespace}" for namespace in namespaces]
        VirtualNetworkManager._run_commands(commands)

    @staticmethod
    def check_and_set_neighbor_table_size(nparties: int) -> None:
        max_value = 2 ** math.ceil(math.log2(nparties ** 2))
        safe_values = [max_value // 2, max_value, 2 * max_value]
        commands = [
            f"sysctl -w net.ipv4.neigh.default.gc_thresh{i + 1}={safe_values[i]}" for i in range(3)
        ]

        # Get current values.
        need_to_increase_sizes = False
        for i in range(3):
            tmp_process = subprocess.Popen(f'sysctl -n -b net.ipv4.neigh.default.gc_thresh{i+1}', shell=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = tmp_process.communicate()
            if tmp_process.returncode == 0 and len(out) > 0:
                current_threshold = int(out.decode())
                if current_threshold < safe_values[i]:
                    need_to_increase_sizes = True
                    break
            else:
                logger.warning("Failed to read the size of the neighbor table. This is most likely caused by NEON being run in a container.")
                logger.warning("A too small neighbor table can lead to connectivity issues when more than 40 parties are used.")
                logger.info("If you run into issues, execute the following commands on the host system:")
                for command in commands:
                    logger.info(command)
                return

        if need_to_increase_sizes:
            logger.info("Neighbor table is too small for number of parties.")
            successful = True
            for i in range(3):
                proc = subprocess.run(['sysctl', '-w', f'net.ipv4.neigh.default.gc_thresh{i + 1} = {safe_values[i]}'])
                if proc.returncode != 0:
                    successful = False
                    break

            if not successful:
                logger.critical(
                    'Failed to increase the size of the neighbor table. This might result in connectivity issues.')
                logger.info('To manually increase the size of the neighbor table, run the following commands:')
                for i in range(3):
                    logger.info(f'sysctl -w net.ipv4.neigh.default.gc_thresh{i + 1}={safe_values[i]}')

    @staticmethod
    def _run_commands(commands: List[str], debug=True) -> None:
        """Runs a list of commands and outputs errors if they appear"""
        for command in commands:
            try:
                logger.debug('+ Executing command"{}"'.format(command))
                subprocess.check_call(command, shell=True)
            except Exception as e:
                if debug:
                    logger.critical(
                        "Command '{}' failed with error {}. Could not work with virtual networks properly.".format(
                            command,
                            e))
                    raise NeonException(
                        f"Command '{command}' failed with error {e}. Could not work with virtual networks properly.")
                else:
                    pass
