#!/usr/bin/env python3

# simple fix so that you can execute XENON and NEON
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

import argparse
import subprocess

from ProgramFiles.helper import get_path_to_mpspdz

import sys
# Needed to have the script be positioned in the NEON directory, but executed in the mp-spdz folder
# in order to import ExternalIO client and domains
sys.path.append(get_path_to_mpspdz())

import ExternalIO.client
import ExternalIO.domains

import time


def receive_send_signal(client, ip_to_ping):
    # Receive message from parties
    # Needed to have the external client synced with the party
    received_stop_keys = []
    for socket in client.sockets:
        o_stream = ExternalIO.client.octetStream()
        o_stream.Receive(socket)

    # ICMP message is used to mark section in pcap file
    p = subprocess.Popen(f"ping -c 1 {ip_to_ping}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (out, _) = p.communicate()

    # Send start signal, so that parties continue their execution
    start_key = 1111
    for socket in client.sockets:
        o_stream = ExternalIO.client.octetStream()
        o_stream.store(start_key)
        o_stream.Send(socket)



if __name__ == '__main__':
    #######PARSER########
    parser = argparse.ArgumentParser(description='Start Signaling Client for Network Traffic Caputre')
    parser.add_argument('ips', help='List of IPs of the compute peers', nargs='+')
    args, unknown_params = parser.parse_known_args()

    # Connect to compute peers
    client_id = 0
    port_num = 14000
    client = ExternalIO.client.Client(args.ips, port_num, client_id)

    # Start computation (and mark it in traffice)
    receive_send_signal(client, args.ips[0])

    # Computation is now finished (and mark it in traffice)
    receive_send_signal(client, args.ips[0])
