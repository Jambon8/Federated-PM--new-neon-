#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
from ProgramFiles.helper import join_path_abs, get_logger
from ProgramFiles import trafficCapture
from xenonMetadata import NetworkMetaMessage, NetworkMetaTraffic, NetworkMetaStep
from xenonConfig import DO_ONLY_MISSING_BENCHMARKS, NETWORK_DELAY, PATH_TO_TEMP, INIT_TEST_PARAMETERS, NUMBER_OF_INIT_TEST_REPETITIONS

from tqdm import tqdm
from multiprocessing.pool import ThreadPool

import re

import subprocess
import os

import logging
logger = get_logger("XENON Network Model")

# whether or not to print the steps of the models
PRINT_STEPS = False

# network latency
network_latency = NETWORK_DELAY / 1000 # convert ms to second
# Determines the size of the time window w.r.t. to network latency
# Used to group packets to abstract messages (e.g. sending a value from 1 to 2, might actually send 2 network packets (as one packet is too small), wo we group them together)
# 0 means no grouping
# 1 up to network_delay is considered one abstract message
# 0.5 means up to network_latency/2 are still considered to be together -> good value
net_latency_precision = 0.5


# Network Captures Dir
network_captures_dir = join_path_abs(PATH_TO_TEMP, "network_captures")

# Where to save the network models
network_models_dir = join_path_abs(PATH_TO_TEMP, "network_models")
if not os.path.exists(network_models_dir):
    os.makedirs(network_models_dir)


class Packet:
    """
    Single TCP packet (only data), with src/dst IPs
    """
    def __init__(self, frame_number, pkt_time, ip_src, ip_dst, frame_len):
        self.time = pkt_time
        self.src = ip_src
        self.dst = ip_dst
        self.data_len = frame_len
        # The packet number in the pcap stream (used for debugging with wireshark)
        self.index = frame_number

    def __str__(self):
        return f"Packet ({self.time:.3f} s): {self.src} -> {self.dst} (data= {self.data_len:4d} bytes [i= {self.index:4d}]"

class Message:
    """
    A single Message from party to another party can consist of multiple packets (and corresponding acks)
    """

    def __init__(self, list_of_packets):
        # Ensure list of packets is ordered by time
        self.packets = list_of_packets

        # Sanity Check and total length computation
        self.total_data_len = self.packets[0].data_len
        for i in range(1,len(list_of_packets)):
            self.total_data_len += self.packets[i].data_len

        # Assume all are correct
        self.time = self.packets[0].time
        self.src = self.packets[0].src
        self.dst = self.packets[0].dst

    def to_network_meta_message(self):
        # Convert message to MetaMessage
        return NetworkMetaMessage(self.src, self.dst, [ tp.data_len for tp in self.packets], self.total_data_len)

    def __str__(self):
        return f"Message ({self.time:.3f}) {self.src} -> {self.dst} (total= {self.total_data_len:3d} bytes) [nr_packets= {len(self.packets)}]"


class Step:
    """
    Corresponds to a single step, e.g. party 1 sends a message to all other parties, or all send to all
    """

    def __init__(self, list_of_messages, n_parties):
        self.time = min(map(lambda x: x.time, list_of_messages))
        self.n_parties = n_parties

        self.list_of_messages = sorted(list_of_messages, key = lambda x: (x.src, x.dst))
            

        # Idividual Totals
        tmp_sending_data_totals = {}
        tmp_receiving_data_totals = {}

        for i in range(1, self.n_parties + 1):
            tmp_sending_data_totals[i] = 0
            tmp_receiving_data_totals[i] = 0

        for msg in self.list_of_messages:
            tmp_sending_data_totals[msg.src] += msg.total_data_len
            tmp_receiving_data_totals[msg.dst] += msg.total_data_len

        # Overal Max
        self.max_sending_data = max(tmp_sending_data_totals.values())
        self.max_receiving_data = max(tmp_receiving_data_totals.values())

        # Overall Global Data sent/received
        tmp_send_global = sum(tmp_sending_data_totals.values())
        tmp_rec_global = sum(tmp_receiving_data_totals.values())
        if tmp_send_global != tmp_rec_global:
            logger.critical("Global Data Send and Receive missmatch")
            exit(1)
        else:
            self.global_data = tmp_send_global

        # Number of messages
        self.num_messages = len(self.list_of_messages)

        # Maximal Single Message
        self.max_single_message = max([msg.total_data_len for msg in self.list_of_messages])


    def to_network_meta_step(self):
        # Convert to network meta step
        list_of_meta_messages = [ msg.to_network_meta_message() for msg in self.list_of_messages ]
        return NetworkMetaStep(list_of_meta_messages, self.max_sending_data, self.max_receiving_data, self.global_data, self.num_messages, self.max_single_message)

    def __str__(self):
        output = f'Time {self.time:.3f} s\n'
        output += f"Max Sending  : {self.max_sending_data:4d} bytes\n"
        output += f"Max Receiving: {self.max_receiving_data:4d} bytes\n"
        output += f"Global Data   : {self.global_data:4d} bytes"

        current_ip = None
        for m in self.list_of_messages:
            output += "\n    "
            if current_ip != m.src:
                current_ip = m.src
                output += f"{m.src}"
            else:
                output += " "
            output += f" -> {m.dst}: {m.total_data_len:3d} bytes "
            output += f"({m.packets[0].data_len:3d}"
            for p in m.packets[1:]:
                output += f", {p.data_len:3d}"
            output += ")"
        
        return output


def generate_network_model_from_capture_file(network_capture_file):
    global network_captures_dir
    global network_models_dir

    # network_capture_file[:-5] -> Remove the ".pcap" from the file name
    network_model_file_name = join_path_abs(network_models_dir, f"{network_capture_file[:-5]}.json")
    if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(network_model_file_name):
        logger.debug(f"{network_capture_file} already done. Skipping.")
        return (network_capture_file, None)

    logger.debug(f"PARSING {network_capture_file}")


    # Construct IP list (we assume the number of parties is below 200)
    # network caputure_file is of form e.g., "multiplication_n=1_m=2_[parties=3].pcap"
    # [:-5] -> remove .pcap
    # rsplit + [1] -> get [parties=3]
    # [9:-1] -> get "3"
    n_parties = int(re.search(r"n=(\d+)", network_capture_file).group(1))
    map_ip_to_id = {}
    for i in range(n_parties):
        map_ip_to_id[f"172.16.1.{11+i}"] = i+1

    ###########################
    # Get Packets of interest #
    ###########################
    # Between the pings and not to/from 172.16.1.10
    pcap_full_path = join_path_abs(network_captures_dir,network_capture_file)

    # Get ICMP numbers
    tcpdump_proc = subprocess.Popen(['tshark', '-r', pcap_full_path, '-Y', 'icmp', '-T', 'fields', '-e', 'frame.number'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = tcpdump_proc.communicate()
    if tcpdump_proc.returncode != 0:
        logger.critical(f"Tshark did not exit correctly (icmp search): {error.decode('utf-8')}")
        exit(1)
    imcp_numbers = output.decode('utf-8').strip().split('\n')

    # sanity Check that exactly 4 icmp messages are present
    if len(imcp_numbers) != 4:
        logger.info(f"Expected number of ICMP Messages missmatch in {network_capture_file}")
        exit(1)
    icmp_start = imcp_numbers[1]
    icmp_end = imcp_numbers[2]

    # Extract traffic of interest
    tcpdump_filter = f"frame.number > {icmp_start} && frame.number < {icmp_end} && ip.addr != 172.16.1.10 && ! tcp.analysis.retransmission"
    tcpdump_proc = subprocess.Popen(['tshark', '-r', pcap_full_path, '-Y', tcpdump_filter, '-T', 'fields', '-e', 'frame.number', '-e',  '_ws.col.Time', '-e', 'ip.src', '-e', 'ip.dst', '-e', 'frame.len'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = tcpdump_proc.communicate()
    if tcpdump_proc.returncode != 0:
        logger.critical(f"Tshark did not exit correctly (extract traffic): {error.decode('utf-8')}")
        exit(1)
    traffic_filtered = output.decode('utf-8').strip().split('\n')
    # Remove any empty lines
    traffic_filtered = [tf for tf in traffic_filtered if tf]

    # Convert to packets
    traffic_of_interest = []
    # Only parse if traffic was captured
    if traffic_filtered:
        # Get time offset from first packet
        time_offset = float(traffic_filtered[0].split('\t')[1])
        for packet in traffic_filtered:
            logger.debug(f"Parsing packet: {packet}")
            frame_number, pkt_time, ip_src, ip_dst, frame_len = packet.split('\t')
            traffic_of_interest.append(Packet(int(frame_number), float(pkt_time) - time_offset, map_ip_to_id[ip_src], map_ip_to_id[ip_dst], int(frame_len)))


    logger.debug(f"Total number of packets: {len(traffic_of_interest)}")
    if logger.level <= logging.DEBUG:
        logger.debug("COMPLETE TRAFFIC OF INTEREST")
        for p in traffic_of_interest:
            logger.debug(p)

    ###############################################################
    # Combine individual Packets to Abstract Messages             #
    # (e.g. party 1 sends a share to party 2)                     #
    ###############################################################
    # If party 1 sends something to party 2, then ideally this would result in a single network packet
    # However, if the content is large, or sent with multiple network packets, then we want to combine them
    # Note, this considers only the TCP layer not smpc protocol logic, so just about sending data
    # E.g. if it is split in three 100 kbit packets (A,B and C), then they are send A-B-C, with no delay in between the,
    # the time difference is only caused by bandwidth and the network interface processing
    # thus eg. A at time 0, B 1ms later than A, and C 1 ms later than B, then sending A or B took 1ms
    # if the delay is set to 20ms, then A,B&C correspong to the same abstract message
    # A packet D 30ms after A, must belong to a reply
    # Note, of course this holds only for the same source and destination adress, respectively. (w.r.t to the same time window)
    # Note, earliest time a reply can be sent is after one delay, as this is minimal time the packet needs to travel between two parties
    # EXAMPLE:
    # Notation: Packet = (packet_id, src, dst, time_from_start)
    # Assume a network delay of 20ms The following packet sequence
    # (A, 1, 2, 0ms), (B, 1, 2, 1ms), (D, 2, 3, 1ms), (C, 1, 2, 2ms), (E, 2, 3, 2ms), (F, 1, 2, 21ms), (G, 2, 1, 21ms)
    # Then the resulting groups would be
    # [A, B, C] -> 1 sent three packets to 2
    # [D, E] -> 2 sent two packets to 3
    # [F] -> 1 sent one packet to 2 (in a later time window)
    # [G] -> 2 sent one packet to 1 (in a later time window, probably a reply ?)


    list_of_messages = []
    
    while traffic_of_interest:
        # Take the first packet
        current_packets = [traffic_of_interest.pop(0)]
        
        index = 0
        while index < len(traffic_of_interest):
            # Note, packets are sorted by time, thus this list is also sorted by time
            # Packets need to be in the same time window
            # Note, do not check ack time, as this can be much later
            if traffic_of_interest[index].time - current_packets[0].time > network_latency * net_latency_precision:
                # We can already break the loop, as only same time window packets are considerd
                break
            elif current_packets[0].src != traffic_of_interest[index].src or current_packets[0].dst != traffic_of_interest[index].dst:
                # if source and destination are not both the same, then go to the next packet
                index += 1
                continue

            # Same time window and same src and dst, thus must belong to the same message
            current_packets.append(traffic_of_interest[index])
            del traffic_of_interest[index]
            # IMPORTANT: Do not increase the index, as we just deleted a packet

        # Append grouped packets to abstract message list
        list_of_messages.append(Message(current_packets))


    logger.debug(f"Total number of messages: {len(list_of_messages)}")
    if logger.level <= logging.DEBUG:
        logger.debug("MESSAGES")
        for m in list_of_messages:
            logger.debug(m)


    ###################################
    # Combine Messages to steps/graph #
    # (time windows, and sort by IP)  #
    ###################################
    # This is similar to the above grouping of packets to abstract messages
    # Here we group multiple abstract messages to steps
    # Meaning concurrently sent messages are grouped together
    # Considering the example from above
    # Step 1: Message(A,B,C) from 1 to 2, and Message(D,E) from 2 to 3
    # Step 2: Message(F) from 1 to 2, and Message(G) from 2 to 1
    # Note between steps there is a network delay


    list_of_steps = []

    while list_of_messages:
        # Take the first packet
        current_msgs = [list_of_messages.pop(0)]
        
        while list_of_messages:
            # Messages must be in the same time window
            if list_of_messages[0].time - current_msgs[0].time > network_latency * net_latency_precision:
                # If next time window, we can abort the search early
                break
            
            # If messages are in the same time window, then they belong to the same step
            current_msgs.append(list_of_messages[0])
            del list_of_messages[0]

        list_of_steps.append(Step(current_msgs, n_parties))

    logger.debug(f"Total number of steps: {len(list_of_steps)}")
    if logger.level <= logging.DEBUG:
        for i, v in enumerate(list_of_steps):
            logger.debug(f"Step {i+1}: {v}")

    ##########################################
    # Get MP-SPDZ print out global data sent #
    ##########################################
    mpspdz_global_data_file = join_path_abs(network_captures_dir, f"{network_capture_file[:-5]}.data")
    with open(mpspdz_global_data_file, 'r') as f:
        tmp_mpspdz_global_data = float(f.readline())
        if tmp_mpspdz_global_data.is_integer():
            mpspdz_global_data = int(tmp_mpspdz_global_data)
        else:
            logger.critical(f"MP-SPDZ global data read is not an integer: {mpspdz_global_data_file}")

    ######################################
    # Convert to NetworkMeta and save it #
    ######################################

    meta_list_steps = [step.to_network_meta_step() for step in list_of_steps]
    nm = NetworkMetaTraffic(meta_list_steps, n_parties, mpspdz_global_data)

    with open(network_model_file_name, 'w') as f:
        logger.debug(f"WRITING Meta: {network_capture_file[:-5]}")
        f.write(nm.to_json())

    # Used for printing
    return (network_capture_file, list_of_steps)



def generate_init_global_data_model(init_global_data_capture_file):
    """
    Extract the real global data of the connection and closing phase (init)
    """
    global network_captures_dir
    global network_models_dir

    # remove .pcap
    init_global_data_file = join_path_abs(network_models_dir, f"{init_global_data_capture_file[:-len('.pcap')]}.txt")
    # Check if all values are present
    if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(init_global_data_file):
        logger.debug(f"INIT for {init_global_data_capture_file} already done. Skipping.")
        return (init_global_data_capture_file, None)

    try:
        # Extract total traffic WITH ICMP messages
        total_traffic_with_icmp = trafficCapture.get_total_traffic_in_pcap(join_path_abs(network_captures_dir, init_global_data_capture_file))
        # Get all ICMP messages
        list_of_all_icmp_messages = trafficCapture.get_icmp_messages_from_pcap(join_path_abs(network_captures_dir, init_global_data_capture_file))
        # Sanity Check for the ICMP messages, and get the overall size of ICMP messages (for compensation)
        icmp_compensation = trafficCapture.check_and_return_icmp_message_length(list_of_all_icmp_messages, 4)
        # Substract ICMP message from total traffic
        total_traffic = total_traffic_with_icmp - icmp_compensation
    except trafficCapture.SafeTcpdumpException as e:
        logger.critical(f"Capture problem: {e}")
        exit(1)
    
    logger.debug(f"Init Traffic for {init_global_data_capture_file}: {total_traffic}")

    # Store the capture sizes
    with open(init_global_data_file, 'w') as f:
        f.write(str(total_traffic))

    return (init_global_data_capture_file, total_traffic)


def get_ack_size_from_init_capture(init_global_data_capture_file):
    global network_captures_dir
    global network_models_dir

    # remove .pcap
    ack_size_file = join_path_abs(network_models_dir, f"ack_size_{init_global_data_capture_file[len('init_global_data_'):-len('.pcap')]}.txt")
    # Check if all values are present
    if DO_ONLY_MISSING_BENCHMARKS and os.path.exists(ack_size_file):
        logger.debug(f"ACK from {init_global_data_capture_file} already done. Skipping.")
        return (init_global_data_capture_file, None)

    # Filter Pure Acks (only ack flag is set, all other are zero, and no selective ACKs)
    tshark_proc = subprocess.Popen(['tshark', '-r', join_path_abs(network_captures_dir, init_global_data_capture_file), '-Y', 'tcp.flags==0x10 && !tcp.options.sack', '-T', 'fields', '-e', 'frame.len'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = tshark_proc.communicate()
    if tshark_proc.returncode != 0:
        logger.critical(f"Tshark did not exit correctly (init traffic): {error.decode('utf-8')}")
        exit(1)
    all_acks = output.decode('utf-8').strip().split('\n')
    set_of_acks = set()
    for ack_size in all_acks:
        set_of_acks.add(int(ack_size))

    if len(set_of_acks) != 1:
        logger.critical(f"ACK size not all the same in {init_global_data_capture_file}: {set_of_acks}. Aborting.")
        exit(1)
    else:
        final_ack_size = set_of_acks.pop()
    
    with open(ack_size_file, 'w') as f:
        f.write(str(final_ack_size))

    return (init_global_data_capture_file, final_ack_size)
        


###############################################################################################
# First extract the abstract steps and messages                                               #
# Second, get the pcap size (and check for errors) of the connection and closing phase (init) #
# Third, determine the ack size (over all init captures)                                      #
###############################################################################################
network_all_captures_dir_files = os.listdir(network_captures_dir)
network_all_captures_dir_files.sort()
network_instruction_captures_dir_files = [ncf for ncf in network_all_captures_dir_files if ncf.endswith('.pcap') and not ncf.startswith("init_global_data_[n=") ]
network_init_captures_dir_files = [ncf for ncf in network_all_captures_dir_files if ncf.endswith('.pcap') and ncf.startswith("init_global_data_[n=") ]

if logger.level <= logging.DEBUG:
    # Abstract Steps and Messages
    for network_capture_file in tqdm(network_instruction_captures_dir_files, desc='Generate Network Models'):
        generate_network_model_from_capture_file(network_capture_file)
    # Init stuff
    for network_init_capture_file in tqdm(network_init_captures_dir_files, desc='Generate Init Models'):
        generate_init_global_data_model(network_init_capture_file)
    # Ack size
    for network_init_capture_file in tqdm(network_init_captures_dir_files, desc='Get ACK sizes'):
        get_ack_size_from_init_capture(network_init_capture_file)
else:
    # Abstract Steps and Messages
    network_models_to_print = {}
    with ThreadPool() as pool:
        for network_capture_file, list_of_steps in tqdm(pool.imap_unordered(generate_network_model_from_capture_file, network_instruction_captures_dir_files), total=len(network_instruction_captures_dir_files), desc='Generate Network Models'):
            network_models_to_print[network_capture_file] = list_of_steps
    # Init stuff
    init_models_to_print = {}
    with ThreadPool() as pool:
        for network_init_capture_file, total_traffic in tqdm(pool.imap_unordered(generate_init_global_data_model, network_init_captures_dir_files), total=len(network_init_captures_dir_files), desc='Generate Init Models'):
            init_models_to_print[network_init_capture_file] = total_traffic
    # Ack size
    overall_acks = set()
    with ThreadPool() as pool:
        for network_init_capture_file, ack_size in tqdm(pool.imap_unordered(get_ack_size_from_init_capture, network_init_captures_dir_files), total=len(network_init_captures_dir_files), desc='Get ACK sizes'):
            overall_acks.add(ack_size)

    # Print steps
    if PRINT_STEPS and logger.level > logging.DEBUG:
        for ncf in network_instruction_captures_dir_files:
            if network_models_to_print[ncf]:
                logger.info(f"Network Model of: {ncf}")
                logger.info(f"Total number of steps: {len(network_models_to_print[ncf])}")
                for i, v in enumerate(network_models_to_print[ncf]):
                    logger.info(f"Step {i+1}: {v}")
                print()
        for network_init_capture_file in network_init_captures_dir_files:
            if init_models_to_print[network_init_capture_file]:
                logger.info(f"Init Traffic for {network_init_capture_file}: {init_models_to_print[network_init_capture_file]/10**6:.2f} MB")
        logger.info(f"All ACK sizes: {sorted(overall_acks)}")
