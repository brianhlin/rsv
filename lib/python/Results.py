#!/usr/bin/env python

# Standard libraries
import os
import re
import sys
import socket
import calendar
import tempfile
import ConfigParser
from time import localtime, strftime, strptime, gmtime

import pdb

UTC_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M:%S %Z"

def timestamp(local=False):
    """ When generating timestamps, we want to use UTC when communicating with
    the remote collector.  For example:
      2010-07-25T05:18:14Z

    However, it's nice to print a more readable time for the local display, for
    example:
      2010-07-25 00:18:14 CDT

    This is consistent with RSVv3
    """
    
    if local:
        return strftime(LOCAL_TIME_FORMAT)
    else:
        return strftime(UTC_TIME_FORMAT, gmtime())


def utc_to_local(utc_timestamp):
    """ Convert a UTC timestamp to a local timestamp.  For example:
    2010-07-25T05:18:14Z -> 2010-07-25 00:18:14 CDT """

    time_struct = strptime(utc_timestamp, UTC_TIME_FORMAT)
    seconds_since_epoch = calendar.timegm(time_struct)
    local_time_struct = localtime(seconds_since_epoch)
    return strftime(LOCAL_TIME_FORMAT, local_time_struct)


class Results:
    """ A class containing code to handle publishing the result records """
    rsv = None

    def __init__(self, rsv):
        self.rsv = rsv


    def wlcg_result(self, metric, output):
        """ Handle WLCG formatted output """

        # Trim detailsData using details-data-trim-length
        trim_length = self.rsv.config.get("rsv", "details-data-trim-length")
        if trim_length > 0:
            pass
            #self.rsv.log("INFO", "Trimming data to %s bytes because details-data-trim-length is set" %
            #        trim_length)
            # TODO - trim detailsData

        # Create a record with a local timestamp.
        local_output = output
        match = re.search("timestamp: ([\w\:\-]+)", local_output)
        if match:
            local_timestamp = utc_to_local(match.group(1))
            local_output = re.sub("timestamp: [\w\-\:]+", "timestamp: %s" % local_timestamp, local_output)

        self.print_result(metric, output, local_output)


    def brief_result(self, metric, status, data):
        """ Handle the "brief" result output """

        self.rsv.log("DEBUG", "In brief_result()")

        #
        # Trim the data appropriately based on details-data-trim-length.
        # A value of 0 means do not trim it.
        #
        trim_length = self.rsv.config.get("rsv", "details-data-trim-length")
        if trim_length > 0:
            self.rsv.log("INFO", "Trimming data to %s bytes because details-data-trim-length is set" %
                         trim_length)
            data = data[:trim_length]

        #
        # We want to print the time different depending on the consumer
        #
        utc_timestamp   = timestamp()
        local_timestamp = timestamp(local=True)

        this_host = socket.getfqdn()

        utc_summary   = self.get_summary(metric, status, this_host, utc_timestamp,   data)
        local_summary = self.get_summary(metric, status, this_host, local_timestamp, data)

        self.print_result(metric, utc_summary, local_summary)



    def print_result(self, metric, utc_summary, local_summary):
        """ Generate a result record for each consumer, and print to the screen """

        #
        # Create a record for each consumer
        #
        for consumer in self.rsv.get_enabled_consumers():
            self.create_consumer_record(metric, consumer, utc_summary, local_summary)

        # 
        # Print the local summary to the screen
        #
        self.rsv.log("INFO", "\n\n") # separate final output from debug output
        self.rsv.echo(local_summary)

        #
        # enhance - should we have different exit codes based on status?  I think
        # that just running a probe successfully should be a 0 exit status, but
        # maybe there should be a different mode?
        #
        # TODO - call clean_up?
        sys.exit(0)



    def get_summary(self, metric, status, this_host, time, data):
        """ Generate a summary string
        Currently metricStatus and summaryData are identical (per RSVv3)
        """

        try:
            metric_type  = metric.config_get("metric-type")
            service_type = metric.config_get("service-type")
        except ConfigParser.NoOptionError:
            self.rsv.log("CRITICAL", "gs1: metric-type or service-type not defined in config")
            sys.exit(1)

        result  = "metricName: %s\n"   % metric.name
        result += "metricType: %s\n"   % metric_type
        result += "timestamp: %s\n"    % time
        result += "metricStatus: %s\n" % status
        result += "serviceType: %s\n"  % service_type
        result += "serviceURI: %s\n"   % metric.host
        result += "gatheredAt: %s\n"   % this_host
        result += "summaryData: %s\n"  % status
        result += "detailsData: %s\n"  % data
        result += "EOT\n"

        return result



    def create_consumer_record(self, metric, consumer, utc_summary, local_summary):
        """ Make a file in the consumer records area """

        # Check/create the directory that we'll put record into
        output_dir = os.path.join(self.rsv.rsv_location, "output", consumer)

        if not self.validate_directory(output_dir):
            self.rsv.log("WARNING", "Cannot write record for consumer '%s'" % consumer)
        else:
            prefix = metric.name + "."
            (file_handle, file_path) = tempfile.mkstemp(prefix=prefix, dir=output_dir)

            self.rsv.log("INFO", "Creating record for %s consumer at '%s'" % (consumer, file_path))

            # TODO - allow for consumer config files that specify which time to use
            # for now we'll just give the html-consumer local time, and UTC to the rest
            if consumer == "html-consumer":
                os.write(file_handle, local_summary)
            else:
                os.write(file_handle, utc_summary)

            os.close(file_handle)

        return


    def validate_directory(self, output_dir):
        """ Validate the directory and create it if it does not exist """

        self.rsv.log("DEBUG", "Validating directory '%s'" % output_dir)

        if os.path.exists(output_dir):
            self.rsv.log("DEBUG", "Directory '%s' already exists" % output_dir, 4)
            if os.access(output_dir, os.W_OK):
                self.rsv.log("DEBUG", "Directory '%s' is writable" % output_dir, 4)
                return True
            else:
                self.rsv.log("WARNING", "Directory '%s'is NOT writable by user '%s'" %
                             (output_dir, self.rsv.get_user()), 4)
                return False


        self.rsv.log("INFO", "Creating directory '%s'" % output_dir, 0)

        if not os.access(os.path.dirname(output_dir), os.W_OK):
            self.rsv.log("WARNING", "insufficient privileges to make directory '%s'." % output_dir, 4)
            return False
        else:
            try:
                os.mkdir(output_dir, 0755)
            except OSError:
                self.rsv.log("WARNING", "Failed to make directory '%s'." % output_dir, 4)
                return False

        return True



    def no_proxy_found(self, metric):
        """ CRITICAL status if we don't have a proxy """
        status = "CRITICAL"
        data   = "No proxy is setup in rsv.conf.\n\n"
        data  += "To use a service certificate (recommended), set the following variables:\n"
        data  += "service_cert, service_key, service_proxy\n\n"
        data  += "To use a user certificate, set the following variable:\n"
        data  += "proxy_file"
        self.brief_result(metric, status, data)



    def missing_user_proxy(self, metric, proxy_file):
        """ Using a user proxy and the specified file does not exist """

        status = "CRITICAL"
        data   = "proxy_file is set in rsv.conf, but the file '%s' does not exist." % proxy_file
        self.brief_result(metric, status, data)



    def expired_user_proxy(self, metric, proxy_file, openssl_output, minutes_til_expiration):
        """ If the user proxy is expired, we cannot renew it like we can with
        the service proxy """

        status = "CRITICAL"
        data   = "Proxy file '%s' is expired (or is expiring within %s minutes)\n\n" % \
                 (proxy_file, minutes_til_expiration)
        data  += "openssl output:\n%s" % openssl_output

        self.brief_result(metric, status, data)


    def service_proxy_renewal_failed(self, metric, cert, key, proxy, openssl_output):
        """ We failed to renew the service proxy using openssl """

        status = "CRITICAL"
        data   = "Proxy file '%s' could not be renewed.\n" % proxy
        data  += "Service cert - %s\n" % cert
        data  += "Service key  - %s\n" % key
        data  += "openssl output:\n%s" % openssl_output

        self.brief_result(metric, status, data)


    def ping_timeout(self, metric, command, error):
        """ The ping command timed out """

        status = "CRITICAL"
        data   = "ping command timed out trying to reach host\n"
        data  += "Error - %s\n\n" % error
        data  += "Troubleshooting:\n"
        data  += "  Manually run the ping command: '%s'\n" % command

        self.brief_result(metric, status, data)


    def ping_failure(self, metric, output):
        """ We cannot ping the remote host """

        status = "CRITICAL"
        data   = "Failed to ping host\n\n"
        data  += "Troubleshooting:\n"
        data  += "  Is the network available?\n"
        data  += "  Is the remote host available?\n\n"
        data  += "Ping output:\n%s" % output

        self.brief_result(metric, status, data)


    def local_job_failed(self, metric, command, output):
        """ Failed to run a metric of type local """
        status = "CRITICAL"
        data   = "Failed to run local job\n\n"
        data  += "Job run:\n%s\n\n" % command
        data  += "Output:\n%s" % output

        self.brief_result(metric, status, data)


    def remote_globus_job_failed(self, metric, command, output):
        """ Failed to run a metric of type remote-globus """
        status = "CRITICAL"
        data   = "Failed to run job via globus-job-run\n\n"
        data  += "Job run:\n%s\n\n" % command
        data  += "Output:\n%s" % output

        self.brief_result(metric, status, data)


    def job_timed_out(self, metric, command, err):
        """ The job exceeded our timeout value """
        status = "CRITICAL"
        data   = "Timeout hit - %s\n\n" % err
        data  += "Job run:\n%s\n\n" % command

        self.brief_result(metric, status, data)
