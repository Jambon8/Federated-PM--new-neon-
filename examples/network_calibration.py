#!/usr/bin/env python3

# simple fix so that you can execute the examples from the examples directory without moving
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
import argparse
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles.operationmode import OperationMode
from ProgramFiles import network




def bandwidth_measurement(neon: NeonHandler, many_senders: bool, many_receivers: bool) -> (float, float, float):
    measurement = neon.multi_bandwidth_measurements(many_senders, many_receivers)
    parties_bandwidth = {}
    for (sender, receiver), (value, unit) in measurement.items():
        if sender not in parties_bandwidth:
            parties_bandwidth[sender] = { "out": 0, "in": 0}
        if receiver not in parties_bandwidth:
            parties_bandwidth[receiver] = { "out": 0, "in": 0}
        parties_bandwidth[sender]["out"] += value
        parties_bandwidth[receiver]["in"] += value

    return parties_bandwidth

def get_average_bandwidth(parties_bandwidth):
    all_out = []
    all_in = []
    for value in parties_bandwidth.values():
        if value["out"] > 0:
            all_out.append(value["out"])
        if value["in"] > 0:
            all_in.append(value["in"])

    average_in = sum(all_in) / len(all_in)
    average_out = sum(all_out) / len(all_out)
    
    return average_out, average_in


def human_readable(bandwidth: float | int) -> str:
    units = ['mbit', 'gbit', 'tbit']
    passing_amount = bandwidth
    passing_unit = 'kbit'
    factor = 1

    for unit in units:
        factor *= 1000
        new_amount = round(bandwidth / factor, 2)
        if new_amount < 1 or abs(new_amount * factor - bandwidth) > 0.05 * factor:
            break
        passing_amount = new_amount
        passing_unit = unit

    return f"{passing_amount}{passing_unit}"

def from_human_readable(bandwidth_str):
    bandwidth_amount = int(bandwidth_str[:-4])
    bandwidth_unit = bandwidth_str[-4:]
    if bandwidth_unit == 'kbit':
        return bandwidth_amount
    elif bandwidth_unit == 'mbit':
        return bandwidth_amount * 10**3
    elif bandwidth_unit == 'gbit':
        return bandwidth_amount * 10 ** 6
    else:
        print(f'Unknown unit: {bandwidth_unit}')
        exit(1)


def print_bandwidth(parties_bandwidth, only_average):
    if not only_average:
        bandwidth_keys = sorted(list(parties_bandwidth.keys()))
        for i in bandwidth_keys:
            print(f'Party {i}: Out = {human_readable(parties_bandwidth[i]["out"])}, In = {human_readable(parties_bandwidth[i]["in"])}')
    average_out, average_in = get_average_bandwidth(parties_bandwidth)
    print(f"Average: Out = {human_readable(average_out)}, In = {human_readable(average_in)}")
            

def do_delay_measurement(neon, delay):
    neon.set_network(network.Network(delay=delay))
    delay = neon.measure_delay()
    return delay

def do_all_bandwidth_tests(neon, outgoing_bandwidth, incoming_bandwidth, only_all_to_all):
    neon.set_network(network.Network(outgoing_bandwidth=outgoing_bandwidth, incoming_bandwidth=incoming_bandwidth))
    if not only_all_to_all:
        one_to_one = bandwidth_measurement(neon, False, False)
        one_to_all = bandwidth_measurement(neon, False, True)
        all_to_one = bandwidth_measurement(neon, True, False)
    else:
        one_to_one = None
        one_to_all = None
        all_to_one = None
    all_to_all = bandwidth_measurement(neon, True, True)
    return one_to_one, one_to_all, all_to_one, all_to_all

def print_all_bandwidth(one_to_one, one_to_all, all_to_one, all_to_all, only_average):
    if one_to_one:
        print("ONE TO ONE")
        print_bandwidth(one_to_one, only_average)
        print()
    if one_to_all:
        print("ONE TO ALL")
        print_bandwidth(one_to_all, only_average)
        print()
    if all_to_one:
        print("ALL TO ONE")
        print_bandwidth(all_to_one, only_average)
        print()
    if all_to_all:
        print("ALL TO ALL")
        print_bandwidth(all_to_all, only_average)
        print()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('n_parties', help='The number of parties involved in the computation.', type=int)
    parser.add_argument('--delay', help='The target delay in ms.', type=int, dest="delay")
    parser.add_argument('--incoming_bandwidth', help='The target bandwidth.', type=str, dest="incoming_bandwidth")
    parser.add_argument('--outgoing_bandwidth', help='The target bandwidth.', type=str, dest="outgoing_bandwidth")
    parser.add_argument('--only_average', help='Print only the averages.', action='store_true', dest="only_average")
    parser.add_argument('--only_all_to_all', help='Do only all-to-all tests.', action='store_true', dest="only_all_to_all")
    parser.add_argument('--skip_base', help='Skip base characterisitcs.', action='store_true', dest="skip_base")
    parser.add_argument('--try_calibrate', help='Trying to Calibrate Network settings. Experimental!', action='store_true', dest="calibrate")
    parser.add_argument('--iterations', '-I', type=int, default=3, dest='iterations',
                        help='The amount of calibration iterations.')
    parser.add_argument('--local_mode', help='Use LOCAL Mode, instead of LOCAL_VIRTUAL.', action='store_true', dest="local_mode")
    args, _ = parser.parse_known_args()

    print("-------------------------")
    print("Measuring Network Characteristics")
    print("This will take some time.")
    print("Please be patient")
    print("-------------------------")

    # Get neon config
    config = NeonConfig.from_config_files()

    # Create new NEON handler
    if args.local_mode:
        neon = NeonHandler(OperationMode.LOCAL, config)
    else:
        neon = NeonHandler(OperationMode.LOCAL_VIRTUAL, config)

    # Set number of parties
    neon.set_number_of_parties(args.n_parties)

    if not args.skip_base or args.calibrate:
        base_delay = do_delay_measurement(neon, None)
        base_one_to_one, base_one_to_all, base_all_to_one, base_all_to_all = do_all_bandwidth_tests(neon, None, None, args.only_all_to_all)

    

    if args.delay:
        neon.set_network(network.Network(delay=f"{args.delay}ms"))
        non_calibrate_delay = neon.measure_delay()
    if args.outgoing_bandwidth:
        nc_out_one_to_one, nc_out_one_to_all, nc_out_all_to_one, nc_out_all_to_all = do_all_bandwidth_tests(neon, args.outgoing_bandwidth, None, args.only_all_to_all)
    if args.incoming_bandwidth:
        nc_in_one_to_one, nc_in_one_to_all, nc_in_all_to_one, nc_in_all_to_all = do_all_bandwidth_tests(neon, None, args.incoming_bandwidth, args.only_all_to_all)
    if args.outgoing_bandwidth and args.incoming_bandwidth:
        nc_both_one_to_one, nc_both_one_to_all, nc_both_all_to_one, nc_both_all_to_all = do_all_bandwidth_tests(neon, args.outgoing_bandwidth, args.incoming_bandwidth, args.only_all_to_all)


    if args.calibrate:
        # Calibrate Delay
        if args.delay:
            print("Calibrating delay")
            target_delay = int(args.delay)

            if base_delay > target_delay:
                print("Real delay to large, cannot calibrate lower delay.")
            else:
                delay_to_set = target_delay - base_delay
                for i in range(args.iterations):
                    print("Testing with delay", delay_to_set)
                    # IMPORTANT: do not add a space before "ms"
                    neon.set_network(network.Network(delay=f"{delay_to_set}ms"))
                    
                    measured_delay = neon.measure_delay()
                    print(f"[Iteration {i+1}] Target delay: {target_delay}ms Measured delay: {measured_delay}ms Old Delay: {delay_to_set} ms")
                    delay_to_set = max(0, delay_to_set - (measured_delay - target_delay) * 0.7 ** i)
                    print(f"New delay to set: {delay_to_set}ms")


        # Calibrate bandwidth output.
        if args.outgoing_bandwidth:
            print("CALIBRATING Outgoing Bandwidth")
            target_out_kbit = from_human_readable(args.outgoing_bandwidth)
            base_avg_out, _ = get_average_bandwidth(base_all_to_all)
            if target_out_kbit > base_avg_out:
                print("Maximal Outgoing bandwidth to large, cannot calibrate larger bandwidth.")
            else:
                out_to_set = target_out_kbit
                for i in range(args.iterations):
                    print("Testing with bandwidth", human_readable(target_out_kbit))
                    neon.set_network(network.Network(outgoing_bandwidth=human_readable(target_out_kbit)))
                    out_bandwidthes = bandwidth_measurement(neon, True, True)
                    avg_out, _ = get_average_bandwidth(out_bandwidthes)
                    print(f"[Iteration {i+1}] Target OUT: {human_readable(target_out_kbit)}, Measured OUT: {human_readable(avg_out)}, Old OUT: {human_readable(out_to_set)}")
                    out_to_set = max(0, out_to_set - (avg_out - target_out_kbit) * 0.7 ** i)
                    print(f"New OUT to set: {human_readable(out_to_set)}")

        # Calibrate bandwidth incoming
        if args.incoming_bandwidth:
            print("CALIBRATING Incoming Bandwidth")
            target_in_kbit = from_human_readable(args.incoming_bandwidth)
            _, base_avg_in = get_average_bandwidth(base_all_to_all)
            if target_in_kbit > base_avg_in:
                print("Maximal Incoming bandwidth to large, cannot calibrate larger bandwidth.")
            else:
                in_to_set = target_in_kbit
                for i in range(args.iterations):
                    print("Testing with bandwidth", human_readable(target_in_kbit))
                    neon.set_network(network.Network(incoming_bandwidth=human_readable(target_in_kbit)))
                    in_bandwidthes = bandwidth_measurement(neon, True, True)
                    _, avg_in = get_average_bandwidth(in_bandwidthes)
                    print(f"[Iteration {i+1}] Target IN: {human_readable(target_in_kbit)}, Measured IN: {human_readable(avg_in)}, Old IN: {human_readable(in_to_set)}")
                    in_to_set = max(0, in_to_set - (avg_in - target_in_kbit) * 0.7 ** i)
                    print(f"New IN to set: {human_readable(in_to_set)}")
    
    
        if args.delay:
            neon.set_network(network.Network(delay=f"{delay_to_set}ms"))
            calibrate_delay = neon.measure_delay()
        if args.outgoing_bandwidth:
            c_out_one_to_one, c_out_one_to_all, c_out_all_to_one, c_out_all_to_all = do_all_bandwidth_tests(neon, outgoing_bandwidth=human_readable(out_to_set), incoming_bandwidth=None, only_all_to_all=args.only_all_to_all)
        if args.incoming_bandwidth:
            c_in_one_to_one, c_in_one_to_all, c_in_all_to_one, c_in_all_to_all = do_all_bandwidth_tests(neon, outgoing_bandwidth=None, incoming_bandwidth=human_readable(in_to_set), only_all_to_all=args.only_all_to_all)
        if args.outgoing_bandwidth and args.incoming_bandwidth:
            c_both_one_to_one, c_both_one_to_all, c_both_all_to_one, c_both_all_to_all = do_all_bandwidth_tests(neon, outgoing_bandwidth=human_readable(out_to_set), incoming_bandwidth=human_readable(in_to_set), only_all_to_all=args.only_all_to_all)




    print()
    if not args.skip_base:
        print("---- Base Characteristics -----------")
        print(f"Delay = {base_delay}\n")
        print_all_bandwidth(base_one_to_one, base_one_to_all, base_all_to_one, base_all_to_all, args.only_average)
        print("")

    if args.delay or args.outgoing_bandwidth or args.incoming_bandwidth:
        print("---- NON-CALLIBRATED Characteristics -----------")
        if args.delay:
            print(f"Delay = {non_calibrate_delay}\n")
        if args.outgoing_bandwidth:
            print(f">> Only Outgoing Bandwidth ({args.outgoing_bandwidth})")
            print_all_bandwidth(nc_out_one_to_one, nc_out_one_to_all, nc_out_all_to_one, nc_out_all_to_all, args.only_average)
        if args.incoming_bandwidth:
            print(f">> Only Incoming Bandwidth ({args.incoming_bandwidth})")
            print_all_bandwidth(nc_in_one_to_one, nc_in_one_to_all, nc_in_all_to_one, nc_in_all_to_all, args.only_average)
        if args.outgoing_bandwidth and args.incoming_bandwidth:
            print(f">> Outgoing and Incoming Bandwidth (out={args.outgoing_bandwidth}, in={args.incoming_bandwidth})")
            print_all_bandwidth(nc_both_one_to_one, nc_both_one_to_all, nc_both_all_to_one, nc_both_all_to_all, args.only_average)
        print()
    
        if args.calibrate:
            print("---- CALLIBRATED Characteristics -----------")
            if args.delay:
                print(f"Delay = {calibrate_delay}\n")
            if args.outgoing_bandwidth:
                print(f">> Only Outgoing Bandwidth ({args.outgoing_bandwidth})")
                print_all_bandwidth(c_out_one_to_one, c_out_one_to_all, c_out_all_to_one, c_out_all_to_all, args.only_average)
            if args.incoming_bandwidth:
                print(f">> Only Incoming Bandwidth ({args.incoming_bandwidth})")
                print_all_bandwidth(c_in_one_to_one, c_in_one_to_all, c_in_all_to_one, c_in_all_to_all, args.only_average)
            if args.outgoing_bandwidth and args.incoming_bandwidth:
                print(f">> Outgoing and Incoming Bandwidth (out={args.outgoing_bandwidth}, in={args.incoming_bandwidth})")
                print_all_bandwidth(c_both_one_to_one, c_both_one_to_all, c_both_all_to_one, c_both_all_to_all, args.only_average)
            print()


            print("--- FINAL SETTINGS ----------------------")
            if args.delay:
                print(f"Final delay: {delay_to_set}ms")
            if args.outgoing_bandwidth:
                print(f"Final outgoing bandwidth: {human_readable(out_to_set)}")
            if args.incoming_bandwidth:
                print(f"Final incoming bandwidth: {human_readable(in_to_set)}")
            print()

