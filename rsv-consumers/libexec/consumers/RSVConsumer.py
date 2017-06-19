#!/usr/bin/env python

import os
import re
import sys
import time
import signal
import subprocess

class InvalidRecordError(Exception):
    """ Custom exception for a bad record format """
    pass

class TimeoutError(Exception):
    """ This defines an Exception that we can use if our system call times out """
    pass

class RSVConsumer:
    """ This is an abstract RSV Consumer base class.  It should be subclassed
    to detail how to handle each type of record """

    rsv_control = os.path.join("/", "usr", "bin", "rsv-control")

    def __init__(self):
        """ Constructor """

        # Register variables
        self.__consumer_done = False
        self.__records_dir = os.path.join("/", "var", "spool", "rsv", "%s-consumer" % self.name)
        self.__log_file = os.path.join("/", "var", "log", "rsv", "consumers", "%s-consumer.output" % self.name)

        # Initialize
        self.check_user()
        self.log("%s-consumer initializing." % (self.name))  # Don't do this until we check the user
        self.parse_arguments()
        self.register_signal_handlers()
        self.validate_records_dir()

        return


    def check_user(self):
        """ If we are currently running as root, do the right thing.  Currently
        the right thing is to exit, but in the future it might be to switch to 'rsv' """

        # We don't want people to accidently run this consumer as root because that may
        # cause permissions problems on log files and directories for the regular RSV user.
        if os.geteuid() == 0:
            self.die("ERROR: The %s-consumer cannot be run as root" % self.name)

        return


    def log(self, msg):
        """ Print a message with a timestamp """

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msg = "%s: %s\n" % (timestamp, msg)
        print msg,

        # If we only print to STDOUT it will end up in a file that Condor will
        # overwrite every time this script executes, so we'll move it to a more
        # permanent log file and rotate it manually.
        try:
            fd = open(self.__log_file, 'a')
            fd.write(msg)
            fd.close()
        except IOError, e:
            sys.stderr.write("Failed to append to log file (%s): %s" % (self.__log_file, e))
            sys.stderr.write(msg)

        return


    def parse_arguments(self):
        """ Specific to each subclass. """
        pass


    def register_signal_handlers(self):
        """ Catch some signals and exit gracefully if we get them """
        signal.signal(signal.SIGINT, self.sigterm_handler)
        signal.signal(signal.SIGTERM, self.sigterm_handler)
        return


    def sigterm_handler(self, signum, frame):
        """ Generic handler for signals """
        self.log("Caught signal #%s.  Exiting after processing current record." % signum)
        self.__consumer_done = True
        return


    def validate_records_dir(self):
        # Where records will be read from
        # This script will delete files from this directory, so it also needs write access.
        if not os.access(self.__records_dir, os.F_OK):
            self.die("ERROR: Records directory does not exist '%s'" % self.__records_dir)
        if not os.access(self.__records_dir, os.R_OK):
            self.die("ERROR: Cannot read records directory '%s'" % self.__records_dir)
        if not os.access(self.__records_dir, os.W_OK):
            self.die("ERROR: Cannot write records directory '%s'" % self.__records_dir)

        return


    def process_files(self, sort_by_time=False, failed_records_dir=None):
        """ Open the records directory and load each file """

        files = os.listdir(self.__records_dir)
        self.log("Processing %s files" % len(files))

        if sort_by_time:
            # For the HTML consumer, we need to sort the files by creation time so that in case
            # multiple records have accumulated for a given metric we want to parse them in order
            # (because the order that we parse them is the order they will show up in the history
            # and the last one we parse will be the current state)
            tmp = []
            for filename in files:
                path = os.path.join(self.__records_dir, filename)
                ctime = os.stat(path).st_ctime
                tmp.append( (ctime, filename) )
            files = map(lambda x: x[1], sorted(tmp))


        for filename in files:
            if self.__consumer_done == 1:
                break

            file_path = os.path.join(self.__records_dir, filename)

            try:
                fh = open(file_path, 'r')
                record = fh.read()
                fh.close()
            except IOError, err:
                self.log("ERROR: Failed to read from file '%s'. Error: %s" % (file_path, err))
                continue
            

            success = False
            try:
                self.process_record(record)
                success = True
            except InvalidRecordError, err:
                self.log("ERROR: Invalid record in file '%s'.  Error: %s" % (file_path, err))
            except Exception, err:
                self.log("ERROR: An unknown exception occurred when processing file '%s'. Error: " % file_path)
                self.log(err)

            if failed_records_dir and not success:
                failed_file = os.path.join(failed_records_dir, filename)
                try:
                    os.rename(file_path, failed_file)
                except OSError, err:
                    # If we cannot move the files then we are going to process them again
                    # So stop processing now to avoid duplicate data.
                    self.die("ERROR: Failed to move record '%s' to '%s'.  Error: %s" %
                             (file_path, failed_file, err))
            else:
                try:
                    os.remove(file_path)
                except OSError, err:
                    # If we cannot remove the files then we are going to process them again
                    # So stop processing now to avoid duplicate data.
                    self.die("ERROR: Failed to remove record '%s'.  Error: %s" % (file_path, err))


    def process_record(self):
        """ Specific to each subclass """
        pass

        
    def parse_wlcg_record(self, raw_record):
        """ Parse a record in WLCG format and return a dict with values.  For the html-consumer
        the timestamp will be in seconds since the epoch.  Example of WLCG record:

        metricName: org.osg.general.ping-host
        metricType: status
        timestamp: 1287068818
        metricStatus: OK
        serviceType: OSG-CE
        serviceURI: osg-edu
        gatheredAt: vdt-itb.cs.wisc.edu
        summaryData: OK
        detailsData: Host osg-edu is alive and responding to pings!
        EOT

        Note: for local probe serviceURI and gatheredAt are replaced by hostName
        """

        record = {}

        # detailsData will always come last, and might be multiple lines
        # Keep state so that once we are in the detailsData section, we put the rest of the
        # lines into detailsData and return when we see EOT
        in_details_data = 0
        for line in raw_record.split('\n'):
            if not in_details_data:
                match = re.match("(\w+):(.*)$", line)
                if match:
                    record[match.group(1)] = match.group(2).strip()
                    if match.group(1) == "detailsData":
                        in_details_data = 1
                else:
                    raise InvalidRecordError("Invalid line:\n\t%s\n\nFull record:\n%s" % (line, raw_record))
            else:
                if re.match("EOT$", line):
                    return record
                else:
                    record["detailsData"] += line + "\n"

        # If we reach this point, it means we did not see EOT.  So the record is invalid
        raise InvalidRecordError("'EOT' marker missing")


    def parse_record(self, raw_record):
        """ Process a record in WLCG format """

        record = self.parse_wlcg_record(raw_record)

        #
        # Check that we got the values we are expecting
        #
        for attribute in ("metricName", "metricType", "metricStatus", "timestamp", "serviceType",
                          "summaryData", "detailsData"):
            if attribute not in record:
                raise InvalidRecordError("Missing %s" % attribute)

        # Marco complained of sometimes getting a blank timestamp
        if record["timestamp"].strip() == "":
            raise InvalidRecordError("timestamp field is empty")

        # We need to have either (hostName) or (serviceURI + gatheredAt)
        host = None
        if "serviceURI" in record and "gatheredAt" in record:
            host = record["serviceURI"]
        elif "hostName" in record:
            host = record["hostName"]
        else:
            raise InvalidRecordError("Missing either hostName or (serviceURI + gatheredAt)")

        return record


    def run_command(self, command, timeout):
        """ Run a shell command with a timeout specified (in seconds).
        Returns:
        1) exit code
        2) STDOUT
        3) STDERR
        """

        try:
            signal.signal(signal.SIGALRM, alarm_handler)
            signal.alarm(timeout)

            # Run and return command
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (stdout, stderr) = p.communicate(None)
            signal.alarm(0)
        except TimeoutError:
            self.log("ERROR: Command timed out (timeout=%s): %s" % (timeout, " ".join(command)))
            #os.kill(child.pid, signal.SIGKILL) # TODO - fix this 
            raise TimeoutError("Command timed out (timeout=%s)" % timeout)

        return (p.returncode, stdout, stderr)
    

    def die(self, msg):
        """ Print an error message and exit with a non-zero status """
        self.log(msg)
        sys.exit(1)


def alarm_handler(signum, frame):
    raise TimeoutError("System call timed out")
