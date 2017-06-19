#!/usr/bin/python

# System libraries
import os 
import pwd
import sys
import signal
from optparse import OptionParser, OptionGroup

# Custom RSV libraries
import RSV
import actions
import run_metric


def process_options(arguments=None):
    usage = """rsv-control:

    Get more information:
    [ --verbose <level> ]
    Level settings - 0=print nothing, 1=normal, 2=info, 3=debug

    Run a one-time test:
    --run [--all-enabled] [--gatekeeper-type|--gk-type gram|condor-ce|cream|nordugrid] --host <HOST> METRIC [METRIC ...]
    --test (same options and behavior as --run but w/o generating records)
    
    Show information about enabled and installed metrics:
    --list [ --wide ] [ --all ] [ --cron-times ] [ <pattern> ]

    Show information about running metrics:
    --job-list [ --host <host-name> ]
    
    Configure desired state of metrics and consumers:
    --enable  --host <host-name> METRIC|CONSUMER [METRIC|CONSUMER ...]
    --disable --host <host-name> METRIC|CONSUMER [METRIC|CONSUMER ...]

    Start and stop metrics and consumers:
    --on  [--host <host-name> [METRIC|CONSUMER ...]]
    --off [--host <host-name> [METRIC|CONSUMER ...]]

    Other commands are available, run with --help to see full usage.
    """

    description = "This script is used to configure and run the RSV monitoring software."
    version = "@@VERSION@@"

    parser = OptionParser(usage=usage, description=description, version=version)
    parser.add_option("-v", "--verbose", dest="verbose", default=1, type="int", metavar="LEVEL",
                      help="Verbosity level (0-3) 0=no output, 1=normal, 2=info, 3=debug. [Default=%default]")
    parser.add_option("-u", "--host", dest="host", default=None,
                      help="Specify the host [and port] to be used by the metric (e.g. host or host:port)")

    group = OptionGroup(parser, "Run a Metric", "Run one or metrics a single time against a "
                        "specified host (even for metrics that are not enabled for that host). "
                        "Example: rsv-control -r --host foo.example.com org.osg.general.osg-version")
    group.add_option("-r", "--run", action="store_true", dest="run", default=False,
                     help="Run the supplied list of metrics against the specified host.")
    group.add_option("--test", action="store_true", dest="test", default=False,
                     help="Same as --run but do not generate records " +
                          "(therefore nothing goes to e.g., HTML page).")
    group.add_option("--all-enabled", action="store_true", dest="all_enabled", default=False,
                     help="Run all enabled metrics serially.")
    group.add_option("--extra-config-file", dest="extra_config_file", default=None,
                     help="Path to another INI-format file containing metric configuration (with --run)")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Information Display Options", "Show enabled metrics and metrics "
                        "that are running in Condor-Cron.")
    group.add_option("-l", "--list", action="store_true", dest="list", default=False,
                     help="List enabled metrics.  If <pattern> is supplied, only metrics " +
                     "matching the regular expression pattern will be displayed.")
    group.add_option("-j", "--job-list", action="store_true", dest="job_list", default=False,
                     help="List metrics/consumers running in condor-cron.  If a host is specified " +
                     "then only metrics from that host are displayed.")
    group.add_option("-w", "--wide", action="store_true", dest="list_wide", default=False,
                     help="No truncation in metric listing")
    group.add_option("--cron-times", action="store_true", dest="list_cron", default=False,
                     help="Show cron times for metrics")
    group.add_option("-a", "--all", action="store_true", dest="list_all", default=False,
                     help="Also display metrics not enabled on any host.")
    group.add_option("--parsable", action="store_true", dest="parsable", default=False,
                     help="Output the job list (-j) in an easy-to-parse format.")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Configuration Options", "Set the desired state of metrics (enable/disable) "
                        "or turn them on and off.  Note that after enabling a metric you must still turn "
                        "it on (similar to vdt-control).  Example: "
                        "rsv-control --enable --host foo.example.com org.osg.general.osg-version")
    group.add_option("--enable", action="store_true", dest="enable", default=False,
                      help="Set the desired state of the metric(s) and/or consumer(s) to enabled.")
    group.add_option("--disable", action="store_true", dest="disable", default=False,
                      help="Set the desired state of the metric(s) and/or consumer(s) to disabled.")
    group.add_option("--on", action="store_true", dest="on", default=False,
                      help="Turn on all enabled metrics.  If a metric is specified, turn on only that metric.")
    group.add_option("--off", action="store_true", dest="off", default=False,
                      help="Turn off all running metrics.  If a metric is specified, turn off only that metric.")
    group.add_option("--arg", action="append", dest="knobs", default=None,
                     help="KEY=VAL to pass to the metric.  This can be specified multiple times.")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Other Options")
    group.add_option("--verify", action="store_true", dest="verify", default=False,
                     help="Run some basic tests to validate your RSV install.")
    group.add_option("--show-config", action="store_true", dest="show_config", default=False,
                     help="Show the configuration for specific metrics.")
    group.add_option("--profile", action="store_true", dest="profile", default=None,
                     help="Run the RSV profiler")
    group.add_option("--no-ping", action="store_true", dest="no_ping", default=False,
                     help="Skip the ping test against the host being monitored")
    group.add_option("--ce-type", "--gatekeeper-type", "--gk-type", dest="ce_type", default="gram",
                     help="Which CE type to use for remote tests via Condor-G. "
                     "Valid values are 'gram' for Globus GRAM, 'htcondor-ce' (or 'condor-ce') for HTCondor-CE ,'cream' for CREAM and 'nordugrid' for Nordugrid")
    parser.add_option_group(group)

    if arguments is None:
        (options, args) = parser.parse_args()
    else:
        (options, args) = parser.parse_args(arguments)

    #
    # Validate options
    #

    # 'test' is the same as 'run' except that we don't generate records.
    # We will implement it by doing all the same things as run but add a check when generating records.
    if options.test:
        options.run = True


    # Check that we got exactly one command
    number_of_commands = len([i for i in [options.run, options.enable, options.disable, options.on,
                                          options.off, options.list, options.job_list, options.verify,
                                          options.show_config, options.profile] if i])

    if number_of_commands > 1:
        parser.error("You can use only one command.")
    if number_of_commands == 0:
        parser.error("You must specify one command.")

    # Check other conditions
    if options.run:
        if options.all_enabled:
            pass
        elif not options.host:
            parser.error("You must provide a host to run metrics against.")
        else:
            # Set options.uri for run_metric.py code
            options.uri = options.host

        if not args and not options.all_enabled:
            parser.error("You must provide a list of metrics to run or else pass the " +
                         "--all-enabled flag to run all enabled metrics")

    if options.ce_type and options.ce_type not in ('gram', 'condor-ce', 'htcondor-ce', 'cream', 'nordugrid'):
        parser.error("Invalid value for --ce-type. "
                     "Valid values are 'gram' for Globus GRAM, 'htcondor-ce' (or 'condor-ce') for HTCondor-CE, 'cream' for CREAM-CE and 'nordugrid' for Nordugrid")

    return options, args



def sigint_handler(signal, frame):
    """ Handle keyboard Ctrl-C """
    print 'Received SIGINT.  Exiting.'
    sys.exit(1)


def main_rsv_control():
    """ Drive the program """

    # Process the command line
    options, args = process_options()

    rsv = RSV.RSV(options)

    # List the metrics
    if options.list:
        if not args:
            return actions.list_metrics(rsv, options, "")
        else:
            return actions.list_metrics(rsv, options, args[0])
    elif options.job_list:
        return actions.job_list(rsv, options.parsable, options.host)
    elif options.show_config:
        return actions.dispatcher(rsv, "show-config", options, args)
    elif options.profile:
        return actions.profile(rsv)
    elif options.verify:
        return actions.verify(rsv)
    else:
        # Check our UID
        this_uid = os.getuid()
        rsv_user = rsv.get_user()
        if this_uid != 0 and this_uid != pwd.getpwnam(rsv_user).pw_uid:
            rsv.echo("ERROR: You must be either root or %s to run these commands: run, on, off, enable, disable" % rsv_user)
            return False
            
        if options.run:
            if options.test:
                rsv.echo('NOTE: Records will not be generated because you specified --test.')
                rsv.echo('      If you want record generation, use --run instead of --test.')
            return run_metric.main(rsv, options, args)
        elif options.on:
            return actions.dispatcher(rsv, "start", options, args)
        elif options.off:
            return actions.dispatcher(rsv, "stop", options, args)
        elif options.enable:
            return actions.dispatcher(rsv, "enable", options, args)
        elif options.disable:
            return actions.dispatcher(rsv, "disable", options, args)

    # We didn't find the request?
    return False


def main():
    signal.signal(signal.SIGINT, sigint_handler)
    PROGNAME = os.path.basename(sys.argv[0])
    if PROGNAME == 'rsv-control' or PROGNAME == 'rsv-control.py':
        if not main_rsv_control():
            sys.exit(1)
        else:
            sys.exit(0)
    else:
        print "Wrong invocation!"
        sys.exit(1)
