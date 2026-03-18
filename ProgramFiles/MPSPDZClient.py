import logging.config
import math
import os
import threading
from typing import List, Dict, Optional

from tqdm import tqdm

from . import helper  # pylint: disable=E0402
from .XIO import LocalXIO, LocalVirtualXIO, DistributedXIO, XIO  # pylint: disable=E0402
from .computationreport import ClientComputationReport
from .operationmode import OperationMode
from .neonconfig import NeonConfig, ReportVerbosityLevel
from .protocol import Protocol, Domain

logging.config.fileConfig(
    helper.join_path_abs(os.path.dirname(__file__), '../config/logging.conf'),
    disable_existing_loggers=False)
# logger = logging.getLogger(__name__)
logger = logging.getLogger("Local Client")


class Client:
    """
    The Client class is responsible for launching MP-SPDZ and capturing and parsing it's output.

    Parameters
    ----------

    operation_mode: OperationMode
        The operation mode of the current NEON instance.
    config: NeonConfig
        The config used in the current NEON instance.
    ip: str
        The IP address of the local client.

    Attributes
    ----------
    """

    operation_mode: OperationMode

    path_to_mpspdz: str
    input: str
    id: int
    ip: str
    config: NeonConfig
    starting_IP: str

    prime: Optional[int]
    R: int
    R_inv: int

    xio: XIO
    __stdout_read_thread: threading.Thread
    __stderr_read_thread: threading.Thread

    last_output: str
    stdout: bytes = None
    stderr: bytes = None
    timers: Dict[str, float] = None

    def __init__(self, operation_mode: OperationMode, config: NeonConfig, ip: str, party_id: int):
        self.operation_mode = operation_mode
        self.input = ""
        self.id = party_id
        self.config = config
        self.ip = ip

        self.path_to_mpspdz = config.local_mpspdz_path

        if operation_mode == OperationMode.LOCAL:
            self.xio = LocalXIO(config)
        elif operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.xio = LocalVirtualXIO(config)
        elif operation_mode == OperationMode.DISTRIBUTED:
            self.xio = DistributedXIO(config, ip)
        else:
            raise Exception(f"Unsupported mode of operation: {operation_mode.name}")

        self.prime = config.prime
        if self.prime:
            self.R = helper.get_R_for_prime(self.prime)
            self.R_inv = helper.mod_inverse(self.R, self.prime)
        self.starting_IP = "127.0.0.1"
        self.last_output = ""

    def start_computation(self, amount_of_parties: int, program: str, protocol: Protocol, direct=False):
        """Performs preperations and launches MP-SPDZ and caputures it's output.

        Parameters
        ----------

        amount_of_parties: int
            Total amount of parties participating in the SMPC.
        program: str
            Program to be executed
        protocol: Protocol
            Protocol to be used for the computations.
        """
        if not program or program == "":
            logger.critical("No program specified for execution. Please use loc.SET_PROGRAM().")
            raise helper.NeonException("No program specified for execution. Please use neon.SET_PROGRAM().")
        logger.debug(f'SMPC started: {self.id}')

        """Start the computation"""
        arg = "./" + protocol.executable
        if self.config.bits_from_squares and protocol.supports_bits_from_squares:  # and type(protocol) not in [protocol.Yao]:
            # Used the have consistent timings, otherwise timings can jump at 20 parties
            arg += " --bits-from-squares"
        if self.config.batch_size != None and protocol.supports_batch_size:
            arg += f' --batch-size {self.config.batch_size}'
        if self.config.bucket_size != None:
            arg += f' --bucket-size {self.config.bucket_size}'
        if self.prime and protocol.domain == Domain.PRIME:
            arg += f' --prime {self.prime}'
        if self.config.unencrypted:
            logger.critical('ENCRYPTION BETWEEN PARTIES IS DISABLED! THE COMPUTATIONS ARE NOT SECURE.')
            arg += ' --unencrypted '

        if protocol.min_number_of_parties != protocol.max_number_of_parties:
            arg += f" --nparties {amount_of_parties}"
        if direct:
            arg += ' --direct '
        # Note, "--output-file ." lets all parties print output to stdout
        arg += f" --output-file . " + \
               f" --verbose " + \
               f" --hostname {self.starting_IP}" + \
               f" --player {self.id}" + \
               f" {program}"

        self.last_output = ''
        if self.operation_mode != OperationMode.LOCAL_VIRTUAL and self.config.incoming_bandwidth:
            logger.warning("Incoming Bandwidth restrition not supported in LOCAL and DISTRIBUTED mode!")

        if self.operation_mode == OperationMode.LOCAL:
            # In local operation mode, the latency will have to be enabled only once.
            if self.id == 0:
                self.xio.enable_latency()
        else:
            self.xio.enable_latency()
        self.xio.execute(arg)

    def start_console_output(self):
        """Start to Capture MP-SPDZ output. This has the advantage that we can output MP-SPDZ output live, which is convenient for debugging."""

        # This code just has to be that way :-)

        # Needed to bypass missing "self" within the stream reader
        STDERR = self.stderr = bytearray()
        STDOUT = self.stdout = bytearray()

        def stream_reader(stream, to_stderr: bool):
            tmp_var = to_stderr  # Python won't accept stderr within result otherwise, I don't know why.

            def result():
                for line in stream:
                    if self.id == 0:
                        logger.info(line.decode().strip())
                    if tmp_var:
                        STDERR.extend(line)
                    else:
                        STDOUT.extend(line)
                stream.close()

            return result

        self.__stderr_read_thread = threading.Thread(target=stream_reader(self.xio.process.stderr, True))
        self.__stderr_read_thread.start()
        self.__stdout_read_thread = threading.Thread(target=stream_reader(self.xio.process.stdout, False))
        self.__stdout_read_thread.start()

    def finish_computation(self) -> ClientComputationReport:
        """
        Waits for the MP-SPDZ client to terminate, perform some cleanup operations and creates a ClientComputationReport.
        Returns
        -------
        ClientComputationReport
            The client's report on the computation.
        """
        self.xio.process.wait()
        self.__stderr_read_thread.join()
        self.__stdout_read_thread.join()

        if self.operation_mode == OperationMode.LOCAL:
            # In local operation mode, the latency will have to be disabled only once.
            if self.id == 0:
                self.xio.disable_latency()
        else:
            self.xio.disable_latency()

        stdout = bytes(self.stdout)
        stderr = bytes(self.stderr)

        # Parse timers and sent data
        timers = {}
        timers_data_sent = {}
        timers_comm_rounds = {}
        cpu_time = None
        total_runtime = None
        data_sent = None
        global_data_sent = None
        global_comm_rounds = None
        for line in stderr.decode("utf-8").splitlines():
            parts = line.split()

            if line.startswith('CPU time ='):
                cpu_time = float(line.split()[3])
            elif line.startswith('Time ='):
                total_runtime = float(line.split()[2])
            elif line.startswith('Time'):
                timer_id = int(parts[0][4:])
                timers[timer_id] = float(parts[2])
                timers_data_sent[timer_id] = parts[4][1:] + ' ' + parts[5][:-1]
                timers_comm_rounds[timer_id] = int(parts[-2])
            if line.startswith('Data sent ='):
                data_sent = parts[3] + " " + parts[4]
                if "rounds" in line:
                    global_comm_rounds = int(parts[6][1:])
            if line.startswith('Global data sent ='):
                global_data_sent = parts[4] + " " + parts[5]
        # Parse private outputs
        private_outputs = None
        binary_output_file = helper.join_path_abs(self.xio.remote_path_to_mpdspz, 'Player-Data',
                                                  f'Binary-Output-P{self.id}-0')
        if self.prime and self.xio.is_file(binary_output_file):
            r = self.xio.read_b(binary_output_file)
            private_outputs = self.read_binary(r)

        self.last_output = self.stdout.decode()

        if self.config.reports_verbosity <= ReportVerbosityLevel.STDOUT:
            # Don't log stderr
            stderr = None
            self.input = None
            self.ip = None
            private_outputs = None
        if self.config.reports_verbosity <= ReportVerbosityLevel.LOW:
            # also do not log stdout
            stdout = None

        return ClientComputationReport(self.ip,
                                       self.input,
                                       stdout,
                                       stderr,
                                       cpu_time,
                                       total_runtime,
                                       data_sent,
                                       global_comm_rounds,
                                       global_data_sent,
                                       timers,
                                       timers_data_sent,
                                       timers_comm_rounds,
                                       private_outputs)

    def get_namespace(self) -> int:
        """The namespaces are from 0-<virtual> with namespace i having ip 172.16.1.(10+i+1) """
        return int(self.ip.split(".")[3]) - 11

    def clean_folders(self):
        """Clean the folders of the respective id."""
        private = helper.join_path_abs(self.path_to_mpspdz,
                                       "Player-Data/Private-Output-{}".format(self.id))
        self.xio.delete(private)

        public = helper.join_path_abs(self.path_to_mpspdz,
                                      "Player-Data/Public-Output-{}".format(self.id))
        self.xio.delete(public)

        persistent_dir = helper.join_path_abs(self.path_to_mpspdz, "Persistence")
        os.makedirs(persistent_dir, exist_ok=True)
        if os.path.isdir(persistent_dir):
            persistent = helper.join_path_abs(self.path_to_mpspdz,
                                              "Persistence/Transactions-P{}.data".format(self.id))
            if os.path.isfile(persistent):
                os.remove(persistent)

    def write_input(self):
        """Write own input to file"""
        input_file = helper.join_path_abs(self.path_to_mpspdz,
                                          "Player-Data/Input-P{}-0".format(self.id))
        self.xio.write(input_file, self.input)

    def _convert_montgomery_to_int(self, data: bytes) -> int:
        """Convert motgomery bytes data to int"""
        tmp = int.from_bytes(data, byteorder='little')
        clear = (tmp * self.R_inv) % self.prime
        return clear

    def _convert_int_to_montgomery(self, value: int) -> bytes:
        assert self.prime

        """Convert int to motgomery bytes data"""
        mont = (value * self.R) % self.prime
        # Computes number of bytes for R, which is the minimum length of a single value stored
        nr_bytes = int(math.log2(self.R) // 8)
        data = mont.to_bytes(nr_bytes, byteorder='little')
        return data

    def write_shares(self, shares: List[int]):
        """Write own shares to file"""
        share_file = helper.join_path_abs(self.path_to_mpspdz, f"Persistence/Transactions-P{self.id}.data")

        mp_spdz_transaction_signature = "1F 00 00 00 00 00 00 00 53 68 61 6D 69 72 20 67 66 70 00 10 00 00 00 80 00 " \
                                        "00 00 00 00 00 00 00 00 00 00 00 1B 80 01"
        share_bytes = bytes.fromhex(mp_spdz_transaction_signature)
        for s in tqdm(shares, desc=f"Writing shares for Party {self.id}", leave=None):
            share_bytes += self._convert_int_to_montgomery(s)
        self.xio.write_b(share_file, share_bytes)

    def read_shares(self) -> List[int]:
        """Read the shares and transform them from their montgomery enconding"""
        share_file = helper.join_path_abs(self.path_to_mpspdz, f"Persistence/Transactions-P{self.id}.data")
        if os.path.exists(share_file):
            data = self.xio.read_b(share_file)
            # Skip the first 39 bytes, as MP-SPDZ writes there some static data
            # See mp_spdz_transaction_signature in write_shares
            return self.read_montgomery(data[39:])
        else:
            return []

    def read_montgomery(self, data: bytes) -> List[int]:
        """Reads montgomery encoded data and converts it to an int"""
        shares = []
        nr_bytes = int(math.log2(self.R) // 8)
        parts = [data[i:i + nr_bytes] for i in range(0, len(data), nr_bytes)]
        for byte in parts:
            # Written Value as integer
            tmp = self._convert_montgomery_to_int(byte)
            shares.append(tmp)
        return shares

    def read_binary(self, data: bytes) -> List[int]:
        """Reads binary encoded data (assumes it is int)"""
        shares = []
        nr_bytes = 8
        parts = [data[i:i + nr_bytes] for i in range(0, len(data), nr_bytes)]
        for byte in parts:
            # Written Value as integer
            tmp = int.from_bytes(byte, byteorder='little')
            shares.append(tmp)
        return shares

    def set_input(self, given_input: str):
        """set the input"""
        self.input = given_input

    @staticmethod
    def is_printing_mpspdz_live() -> bool:
        """
        Returns wheter the Client will print MP-SPDZ's output live during execution.
        """
        return logger.isEnabledFor(logging.DEBUG)
