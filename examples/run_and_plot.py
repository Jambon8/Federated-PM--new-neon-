#!/usr/bin/env python3

# simple fix so that you can execute the examples from the examples directory without moving
import sys
sys.path.insert(0, sys.path[0].rsplit("/",1)[0])

# Actual imports
import argparse
from typing import List, Dict, Tuple

import matplotlib.pyplot

from ProgramFiles.computationreport import ComputationReport, ComputationReportList
from ProgramFiles.helper import min_median_max

from ProgramFiles.operationmode import OperationMode
from ProgramFiles.neonconfig import NeonConfig
from ProgramFiles.neonhandler import NeonHandler
from ProgramFiles import protocol
from ProgramFiles import network



def own_parameters(neon: NeonHandler) -> None:
    # This function could be used to set custom parameters.
    # For example:
    # neon.set_batch_size(20 * neon.n_parties)
    neon.set_input(0, '1 2 3 4')
    neon.set_input(1, '5 6 7 8')
    neon.set_input(2, '5 6 7 8')
    neon.set_bits_from_squares(True)


def run_computations(neon: NeonHandler, args) -> ComputationReportList:
    program = args.program
    parties = [int(x) for x in args.parties.split(',')]
    iterations = args.iterations
    for n_parties in parties:
        neon.set_number_of_parties(n_parties)
        if args.delay or args.incoming_bandwidth or args.outgoing_bandwidth:
            neon.set_network(network.Network(delay=args.delay, incoming_bandwidth=args.incoming_bandwidth, outgoing_bandwidth=args.outgoing_bandwidth))
        neon.set_program(program)

        own_parameters(neon)

        for _ in range(iterations):
            neon.smpc()
    return neon.get_computation_reports()


def plot_by_parties_mean(reports: ComputationReportList, neon: NeonHandler) -> None:
    reports_grouped: List[Tuple[int, ComputationReportList]] = reports.group_by_number_of_parties_sorted()

    new_n_parties = []
    mins = []
    medians = []
    maxs = []

    for (n_parties, sub_reports) in reports_grouped:
        new_n_parties.append(n_parties)
        min, median, max = sub_reports.get_total_runtime_min_median_max()
        mins.append(min)
        medians.append(median)
        maxs.append(max)

    # Plot the values
    fig, ax = matplotlib.pyplot.subplots()
    
    fig.suptitle("{} ({}, {}, {}, {})".format(reports[0].program, str(reports[0].operation_mode).split(".")[1], reports[0].delay, reports[0].outgoing_bandwidth, reports[0].incoming_bandwidth))
    
    ax.set_xlabel('Parties')
    ax.set_ylabel('Runtime [s]')
    ax.set_title('Mean, and Max/Min Runtime and Standard Deviation')
    ax.xaxis.set_ticks(new_n_parties)
    
    ax.plot(new_n_parties, maxs, color='red', linestyle = 'dotted', label="Max")
    ax.plot(new_n_parties, medians, color='blue', label="Median")
    ax.plot(new_n_parties, mins, color='green', linestyle = 'dotted', label="Min")
    
    ax.grid()
    ax.legend()
    
    neon.save_matplot_figure(fig)
    matplotlib.pyplot.show()
    
    
def plot_by_parties_average(reports: List[ComputationReport], neon: NeonHandler) -> None:
    # Group by number of parties
    reports_grouped = reports.group_by_number_of_parties_sorted()

    # Extract list of number of parties, average times and standard deviations
    # In addition, average time plus/minus standard deviation
    new_n_parties = []
    new_averages = []
    new_standards = []
    new_avg_plus_stand = []
    new_avg_minus_stand = []
    for n, sub_reports in reports_grouped:
        tmp_avg, tmp_stand = sub_reports.get_total_runtime_average_and_standard_deviation()
        new_n_parties.append(n)
        new_averages.append(tmp_avg)
        new_standards.append(tmp_stand)
        new_avg_plus_stand.append(tmp_avg + tmp_stand)
        new_avg_minus_stand.append(tmp_avg - tmp_stand)

    # Plot the values
    fig, ax = matplotlib.pyplot.subplots()

    fig.suptitle("{} ({}, {}, {}, {})".format(reports[0].program, str(reports[0].operation_mode).split(".")[1], reports[0].delay, reports[0].outgoing_bandwidth, reports[0].incoming_bandwidth))
    ax.set_xlabel('Parties')
    ax.set_ylabel('Runtime [s]')
    ax.set_title('Total Runtime and Standard Deviation')
    ax.xaxis.set_ticks(new_n_parties)

    ax.plot(new_n_parties, new_avg_plus_stand, c='green', linestyle = 'dotted', label="Standard Deviation")
    ax.plot(new_n_parties, new_averages, c='green', label="Average")
    ax.plot(new_n_parties, new_avg_minus_stand, c='green', linestyle = 'dotted')

    ax.grid()
    ax.legend()

    neon.save_matplot_figure(fig)
    matplotlib.pyplot.show()
    
    
def plot_by_parties_communication_rounds(reports: ComputationReportList, neon: NeonHandler) -> None:
    reports_grouped: List[Tuple[int, ComputationReportList]] = reports.group_by_number_of_parties_sorted()

    new_n_parties = []
    mins = []
    medians = []
    maxs = []

    for (n_parties, sub_reports) in reports_grouped:
        # communication_rounds = list(map(lambda x: float(x.split()[0]),
        #                            sum([[client_report.communication_rounds for client_report in report.client_reports] for report in sub_reports], start=[])))
        communication_rounds = sum([[client_report.communication_rounds for client_report in
                                              report.client_reports] for report in sub_reports], start=[])

        new_n_parties.append(n_parties)
        min, median, max = min_median_max(communication_rounds)
        mins.append(min)
        medians.append(median)
        maxs.append(max)

    # Plot the values
    fig, ax = matplotlib.pyplot.subplots()

    fig.suptitle(
        "{} ({}, {}, {}, {})".format(reports[0].program, str(reports[0].operation_mode).split(".")[1], reports[0].delay,
                                     reports[0].outgoing_bandwidth, reports[0].incoming_bandwidth))

    ax.set_xlabel('Parties')
    ax.set_ylabel('Rounds')
    ax.set_title('Mean, and Max/Min Communication Rounds')
    ax.xaxis.set_ticks(new_n_parties)

    ax.plot(new_n_parties, maxs, color='red', linestyle = 'dotted', label="Max")
    ax.plot(new_n_parties, medians, color='blue', label="Median")
    ax.plot(new_n_parties, mins, color='green', linestyle = 'dotted', label="Min")

    ax.grid()
    ax.legend()

    neon.save_matplot_figure(fig)
    matplotlib.pyplot.show()
    

def plot_by_parties_timers(reports: List[ComputationReport], neon: NeonHandler) -> None:
    # Group by number of parties
    reports_grouped = reports.group_by_number_of_parties_sorted()

    # Extract list of number of parties
    new_n_parties = []
    for n, sub_reports in reports_grouped:
        new_n_parties.append(n)

    # Get all timer names (select first group, select report list (is second entry in tuple), and then select the first report
    new_timers = reports_grouped[0][1][0].timer_names.items()
    new_list_timers = {}
    for key, label in new_timers:
        new_list_timers[label] = []
        for n, sub_reports in reports_grouped:
            tmp_avg, tmp_stand = sub_reports.get_timer_average_and_standard_deviation(key)
            new_list_timers[label].append(tmp_avg)

    # Plot the values
    fig, ax = matplotlib.pyplot.subplots()

    fig.suptitle("{} ({}, {}, {}, {})".format(reports[0].program, str(reports[0].operation_mode).split(".")[1], reports[0].delay, reports[0].outgoing_bandwidth, reports[0].incoming_bandwidth))
    ax.set_xlabel('Parties')
    ax.set_ylabel('Runtime [s]')
    ax.set_title('Individual Timers')
    ax.xaxis.set_ticks(new_n_parties)
    
    ax.stackplot(new_n_parties, new_list_timers.values(), labels=new_list_timers.keys(), alpha=0.8)
    
    ax.grid()
    ax.legend()

    neon.save_matplot_figure(fig)
    matplotlib.pyplot.show()




if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('program', help='The program whose runtime should be analyzed.', type=str)
    parser.add_argument('--iterations', help='The number of iterations to run a program.', type=int, default=5)
    parser.add_argument('--delay', type=str, help='Artificial delay.', dest='delay', default=None)
    parser.add_argument('--incoming_bandwidth', type=str, help='Artificial incoming bandwidth restriction.', dest='incoming_bandwidth', default=None)
    parser.add_argument('--outgoing_bandwidth', type=str, help='Artificial bandwidth restriction.', dest='outgoing_bandwidth', default=None)
    parser.add_argument('--parties', type=str, help='Comma-seperated list of parties to run computations with.',
                        dest='parties', default='3,5,8,10')
    parser.add_argument('--distributed', help='If set perform the SMPC execution distributed.', action='store_true', default=False)
    parser.add_argument('--virtual', help='Create a virtual network where NEON can run.',action='store_true', default=False)
    parser.add_argument('--importLog', type=str, dest='import_log', default=None,
                        help='Don\'t run the computations, import log from timestamp instead.')
    parser.add_argument('--importFolder', type=str, dest='import_folder', default=None,
                        help='Don\'t run the computations, import log from folder instead.')
    args, _ = parser.parse_known_args()

    if args.import_log:
        # Read logs from file
        reports = ComputationReportList.from_logs(args.import_log)
    elif args.import_folder:
        # Read logs from folder
        reports = ComputationReportList.from_folder(args.import_folder)
    else:
        config = NeonConfig.from_config_files()
        
        
        if args.distributed and not args.virtual:
            op_mode = OperationMode.DISTRIBUTED
        elif args.virtual and not args.distributed:
            op_mode = OperationMode.LOCAL_VIRTUAL
        elif not args.distributed and not args.virtual:
            op_mode = OperationMode.LOCAL
        else:
            raise NotImplementedError("Specify only one network setting or none")

        neon = NeonHandler(op_mode, config)
        neon.set_protocol(protocol.Shamir)
        reports = run_computations(neon, args)

    plot_by_parties_mean(reports, neon)
    plot_by_parties_average(reports, neon)
    plot_by_parties_communication_rounds(reports, neon)
    plot_by_parties_timers(reports, neon)


