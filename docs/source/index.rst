Welcome to NEON's documentation!
=================================

NEON (NEtwork simulatiON and benchmarking wrapper) is a framework that acts as a wrapper for `MP-SPDZ <https://github.com/data61/MP-SPDZ>`_.
It provides an easy and reliable way to manipulate the environment and parameters like different number of parties or network settings for evaluating SMPC protocols in different settings.

NEON's main features are:

- Simulate different network settings (see :ref:`networking`)
- Set protocol, program, inputs, secrets, number of parties, batch size, ...
- Get measurements like runtime, or MP-SPDZ global data (with extensive reports)
- Named Timers for MP-SPDZ programs
- Variable substitution for MP-SPDZ programs


If you are new to NEON, consider the following:

1. The :ref:`quickstart` guide
2. :ref:`ProgramFiles.neonhandler module`
3. Check the examples in ``examples`` and the programs in ``Programs``.


Compatibility
-------------
NEON has been tested with Debian 12, and it is developed primarily for MP-SPDZ version 0.3.6 and the SMPC primitive ``Shamir``. NEON has options to use other MP-SPDZ versions and other SMPC primitives. However, compatibility with other MP-SPDZ versions as well as other SMPC primitives has only been partially tested and may be very limited, so consider them untested and experimental.


Important
---------

NEON executes MP-SPDZ with two main defaults:

- ``bits-from-squares`` is enabled by default, as this prevents an unexpected runtime change for around 20 parties.
- for protocols in the prime/field domain the default prime is currently set to ``170141183460469231731687303715885907969`` (which should be the same as used by MP-SPDZ). This is required to be able to read and write shares written/read by MP-SPDZ.

For more information and any other defaults please check :ref:`ProgramFiles.neonconfig module` and :ref:`ProgramFiles.neonhandler module`. Also check the `MP-SPDZ docs <https://mp-spdz.readthedocs.io/en/latest/>`_ for any default settings of MP-SPDZ.

Furthermore, NEON does **not** capture/output the real/physical network traffic, and outputs only the traffic that MP-SPDZ also outputs. However, if you want to capture the real network traffic, you can take a look at :ref:`ProgramFiles.trafficCapture module` as a starting point.

.. toctree::
   :maxdepth: 2
   :caption: Content:

   quickstart
   networking
   modules


Indices and tables
==================

* :ref:`genindex`
* :ref:`search`
