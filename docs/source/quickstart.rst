Quickstart
##########

Installation (Linux)
********************
1. Clone this repository
2. Download and install MP-SPDZ by running ``python3 setup.py install-mpspdz --version 0.3.6`` (or use ``python3 setup.py install-mpspdz`` for the latest version (untested))
3. Execute the example using ``python3 examples/example_simple.py``

Your first code
******************

NEON can be used as library. NEON then takes care of the rest, like initializing virtual namespaces.

Start of by creating the file ``first_code.py`` in the root directory of NEON folder with the following content (Note, a similar file already exists as ``examples/example_simple.py``):

.. code-block:: python
    :linenos:

    from ProgramFiles.operationmode import OperationMode
    from ProgramFiles.neonconfig import NeonConfig
    from ProgramFiles.neonhandler import NeonHandler
    from ProgramFiles import protocol

    config = NeonConfig.from_config_files()
    neon = NeonHandler(OperationMode.LOCAL, config)

    neon.set_protocol(protocol.Shamir)
    neon.set_number_of_parties(3)
    neon.set_all_inputs("1 2 3 4")

    neon.set_program("example")
    report = neon.smpc()

Now, you can execute your script using ``python3 first_code.py``. Let's quickly go through the code:

1. First some imports.
2. Define a config from the files in the config folder.
3. Then define the neonhandler, and set the operation mode to local (communication over loopback).
4. Inside, set the Protocol (Shamir), specify that we are performing a computation with three participating parties. In addition, set some dummy inputs.
5. Tell NEON that we want to execute the MP-SPDZ program called "example". That program will have to be located in the ``Programs`` folder.
6. Finally, we tell NEON to execute the SMPC program. NEON will use MP-SPDZ to compile the program if required, set up the environment, and execute the protocol with MP-SPDZ.

The ``NeonHandler`` gives you many tools to configure the computation parameters to your liking. You can find a comprehensive documentation at :ref:`ProgramFiles.neonhandler module`.

Note, each ``report`` has a function ``run_was_successfull()`` that can be used to determine whether the execution succeeded or not. See ``examples/example.py`` how it can be used.

You can find more examples in the ``examples`` folder.

A Note on Setup
***************

The setup utility has also some further features:

* ``install-mpspdz`` downloads and fresh-installs the latest MP-SPDZ version. You can install a different version by providing the ``--version`` parameter. When ``--version git`` is provided, the utility will clone the MP-SPDZ master branch and compile it instead of downloading a pre-compiled version.
* ``clean-virtual`` destroys all NEON related virtual namespaces. NEON usually does this on its own upon terminating, but it still may be useful in case things go horribly wrong.

In addition, there is an **untested** ``prepare-distributed`` option (previously developed, and then no longer maintained due to focusing on LOCAL_VIRTUAL mode), that distributes the local MP-SPDZ installation to remote machines over SSH. **IMPORTANT** whenever you change (increase or exchange) the servers in ``config/ip.conf``, then you need to re-run ``prepare-distributed``. This ensures that all servers/clients use the same MP-SPDZ version, and that all certificates are distributed (required by MP-SPDZ). **WARNING:** This option assumes that these servers are solely used for the execution of NEON/MP-SPDZ, for two reasons: One, other users may impact the performance of runtimes and NEON requires root to change network restrictions, which might influence other users. Second, the directory defined in ``folderlocation`` in ``config/distributed.conf`` is managed by NEON and shall not be messed with manually.