Networking
##########

Modes of operation
******************

NEON has two main modes of operation:

- **LOCAL**: All parties run on the same machine and communicate over localhost.
- **LOCAL_VIRTUAL**: All parties run on the same machine, but a virtual network is created to simulate a distributed setting.

There is also a third **untested** mode:

- **DISTRIBUTED**: All parties run on different machines, and communicate over LAN.


These modes support to varying degree setting network restrictions like bandwidth and delay, but with **certain restrictions**. See below for further details.


Depending on what you want to do we recommend the following mode:

- *LOCAL* mode is most useful without any network restrictions to quickly run/test a protocol on the local machine.
- *LOCAL_VIRTUAL* is most useful for the actual evaluation of a protocol in a specific network setting. The advantage is that there is no need for a complicated network setup, as everything is handled by NEON.
- *DISTRIBUTED* is untested, but would allow to evaluate a protocol with different machines. This setting was included in an early development stage of NEON, but at some point no longer actively maintained, as we mainly focused on the *LOCAL_VIRTUAL* mode. However, we did not remove it as someone might want to use it, and it might still work, or can hopefully be easily fixed.



How latency and bandwidth are simulated
***************************************

Simulation of bandwidth restrictions (incoming and outgoing) and delays is done by using the tool ``tc`` (traffic control)  with ``netem`` (network emulator),

- *LOCAL* mode: We create a ``qdisc`` with the desired properties on the output of the loopback interface. Only delay and outgoing_bandwidth are applied. There is **no** incoming_bandwidth limitation applied. 

- *LOCAL_VIRTUAL* mode: A virtual network is created on the local machine that allows each party to sit behind its own virtual network interface. NEON creates a Linux bridge, separate namespaces (for each party), virtual network interfaces, and assigns IP addresses (172.16.1.11-254). A ``qdisc`` is added on the outgoing virtual interface of each namespace that applies the outgoing_bandwidth restriction and delay to outgoing data. Also, a ``qdisc`` is added to the incoming interface to each namespace from the bridge which applies only the incoming_bandwidth restrictions. Each client/party (MP-SPDZ process) is then later started in its own namespace (each with its own network interface).

- *DISTRIBUTED* mode (**untested**): It (should) add an outgoing ``qdisc`` to the interfaces of each distributed machine, so each outgoing data get its own set of latency and outgoing_bandwidth restrictions. We do not add restrictions to incoming data, i.e., no incoming_bandwidth limitations.



Note, applying any network restrictions requires root privileges. In addition, *LOCAL_VIRTUAL* requires root privileges, too, in order to create the virtual network. So, *LOCAL* mode without any restrictions can be used without root privileges.


Restrictions and Limitations
****************************

First, we use ``qdisc`` and ``netem`` to set delay, incoming and outgoing bandwidth limitations. Depending on your setup/system these restrictions (especially w.r.t. bandwidth) might not always be satisfiable. Also, ``qdisc`` and ``netem`` might not be able to always keep the limits accurately. Thus, the set/wanted and actual/real limitations can differ. Especially, there will always be a small delay, and bandwidth restriction due to system resource limitations, even if no network restrictions are set. This means, that the restrictions set in NEON are added on top of the base restrictions.

You can obtain a base measurement for the *LOCAL* and *LOCAL_VIRTUAL* mode with ``examples/network_calibration.py``:

- For *LOCAL*: ``python3 examples/network_calibration.py <number_of_parties> --local_mode``
- For *LOCAL_VIRTUAL*: ``python3 examples/network_calibration.py <number_of_parties>``


Note, *LOCAL* and *LOCAL_VIRTUAL* mode run all parties on the same machine, and thus share the (same) CPU. For good/accurate results the number of cores should be at least the number of parties, preferably more. Note, for non computation heavy protocols, fewer cores might suffice.


Further restrictions and considerations for each mode are given in the following:

LOCAL Mode Limitations
----------------------

All parties communicate over localhost. Any latency and outgoing_bandwidth restriction are added to the localhost network interface. The local network interface is used by all parties simultaneously, and thus, the restrictions are w.r.t. to *all* communications at once. If multiple parties send data simultaneously they have to share the bandwidth, while if only one party sends, it can use the complete bandwidth. Therefore, choosing a good outgoing_bandwidth can be very complicated. Furthermore, no incoming_bandwidth limitation can be applied. Hence, this mode should be avoided if accurate measurements are desired. However, this mode can be used as a lower bound (no limits set), or upper bound (limits set as for a single party), or just to obtain some rough numbers.

In addition, if no restrictions (no outgoing_bandwidth, and no delay) are set, then this mode is very useful to test the general function (during development) of a program, because of the low overhead in NEON and low natural bandwidth/latency.

Note, you can use the ``examples/network_calibration.py`` to test the network behavior for different restrictions, e.g., ``python3 examples/network_calibration.py 3 --incoming_bandwidth 1gbit --outgoing_bandwidth 1gbit --local_mode``.


LOCAL_VIRTUAL Mode Limitations
------------------------------

The bandwidth and delay restrictions represent/simulate a distributed setting, as they are applied for each party individually. Note, that the underlying system (and ``qdisc`` and ``netem``) might still pose upper/lower limits on the bandwidth and delay that is achievable.

When working with LXC containers, aside from root, nesting needs to be enabled to create namespaces.

This mode should be used if one wants accurate results, but only wants to utilize a single machine while having the appropriate privileges.

Note, you can use the ``examples/network_calibration.py`` to test the network behavior for different restrictions, e.g., ``python3 examples/network_calibration.py 3 --incoming_bandwidth 1gbit --outgoing_bandwidth 1gbit``.


DISTRIBUTED Mode Limitations
----------------------------

**WARNING: This mode is UNTESTED.** It was no longer maintained due to focusing on the LOCAL_VIRTUAL mode. However, it might still work due to the design of the XIO in NEON.

This setting requires one machine/server for each party. And the machines need to be able to communicate. You need to know their IPs and add them in ``config/ip.conf``. The path to the SSH key able to access the servers can be specified in ``config/distributed.conf``.

Only delay and outgoing_bandwidth restrictions are applied, i.e., no incoming_bandwidth restrictions. The artificial restrictions are added on top of any existing restrictions on these links, like existing bandwidth and delay. Thus, restricting the achievable delay and outgoing_bandwidth possible. Furthermore, as no incoming_bandwidth limitation is applied, this can result in unrealistic times when several parties send data to a single party.

Even though this mode can also be used with containers, in this case one can simply use the LOCAL_VIRTUAL mode instead.



Large Number of parties
***********************

If you use the LOCAL_VIRTUAL mode and a large number of parties, and the parties cannot connect, this is probably caused by having "net.ipv4.neigh.default.gc_thresh3" set to low. Set this value ("sysctl -w net.ipv4.neigh.default.gc_thresh3=<value>") to about 2 * number_of_parties ^ 2.
Note, there is a check and warning implemented in NEON, and it also additionally tries to set it to safer values. However, setting or even reading these values might not work inside a container.


Further Notes
*************
The actual network setting can be set in NEON by using ``set_network_setting(network_setting)`` where ``network_setting = Network(delay, incoming_bandwidth, outgoing_bandwidth)``. See ``ProgramFiles/network.py`` for some pre-defined settings, e.g., Unlimited, LAN, WAN, ..., and also check ``examples/example.py`` and :ref:`ProgramFiles.network module`.

Furthermore, you can use ``examples/network_calibration.py`` to test the performance of different parameters. Use ``python3 examples/network_calibration.py --help`` for the available options.