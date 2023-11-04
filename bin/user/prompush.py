#!/usr/bin/env python

"""
sample output from simulator

key          value               type
----------------------------------------
outHumidity  79.9980573766       gauge
maxSolarRad  960.080999341
altimeter    32.0845040681       guage
heatindex    32.4567414016       gauge
radiation    748.170598504       gauge
inDewpoint   31.0785251193       gauge
inTemp       63.0012950398       gauge
barometer    31.0999352459       gauge
windchill    32.4567414016
dewpoint     26.9867627099       gauge
windrun      1.20018113179e-05
rain         0.0                 gauge
humidex      32.4567414016       gauge
pressure     31.0999352459       gauge
ET           0.480818085118
rainRate     0.0                 gauge
usUnits      1
appTemp      28.2115054547       gauge
UV           10.4743883791       gauge
dateTime     1466708460.0
windDir      359.988202072       gauge
outTemp      32.4567414016       gauge
windSpeed    0.00032377056758    gauge
inHumidity   29.9974099203       gauge
windGust     0.0004618843668     gauge
windGustDir  359.986143469       gauge
cloudbase    2122.17697538       gauge

"""

import weeutil.weeutil
import weewx.restx

weather_metrics = {
    'weather_outHumidity': 'gauge',
    'weather_maxSolarRad': 'gauge',
    'weather_altimeter': 'gauge',
    'weather_heatindex': 'gauge',
    'weather_radiation': 'gauge',
    'weather_inDewpoint': 'gauge',
    'weather_inTemp': 'gauge',
    'weather_barometer': 'gauge',
    'weather_extraTemp1': 'gauge',
    'weather_extraTemp2': 'gauge',
    'weather_extraTemp3': 'gauge',
    'weather_windchill': 'gauge',
    'weather_dewpoint': 'gauge',
    # 'windrun':
    'weather_rain': 'gauge',
    'weather_humidex': 'gauge',
    'weather_pressure': 'gauge',
    # ET':
    'weather_rainRate': 'gauge',
    # 'usUnits':
    'weather_appTemp': 'gauge',
    'weather_UV': 'gauge',
    # dateTime
    'weather_windDir': 'gauge',
    'weather_outTemp': 'gauge',
    'weather_windSpeed': 'gauge',
    'weather_inHumidity': 'gauge',
    'weather_windGust': 'gauge',
    'weather_windGustDir': 'gauge',
    'weather_cloudbase': 'gauge',
    'co2': 'gauge',
    'pm10': 'gauge',
    'pm2_5': 'gauge',
    'windrun': 'gauge'
}

__version__ = '1.0.3'

import weewx
import weewx.restx
import weeutil.weeutil

import requests

import queue as Queue
import sys
import syslog
import logging


class PromPush(weewx.restx.StdRESTful):
    """

    sends weewx weather records to a prometheus pushgateway using the
    prometheus_client library

    """

    def __init__(self, engine, config_dict):
        super(PromPush, self).__init__(engine, config_dict)
        try:
            _prom_dict = weeutil.weeutil.accumulateLeaves(
                config_dict['StdRESTful']['PromPush'], max_level=1)
        except KeyError as e:
            logging.error("config error: missing parameter %s" % e)
            return

        _manager_dict = weewx.manager.get_manager_dict(
            config_dict['DataBindings'], config_dict['Databases'], 'wx_binding')

        self.loop_queue = Queue.Queue()
        self.loop_thread = PromPushThread(self.loop_queue, _manager_dict,
                                          **_prom_dict)
        self.loop_thread.start()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        logging.info("data will be sent to pushgateway at %s:%s" %
                     (_prom_dict['host'], _prom_dict['port']))

    def new_loop_packet(self, event):
        self.loop_queue.put(event.packet)


class PromPushThread(weewx.restx.RESTThread):
    """
    thread for sending data to the configured prometheus pushgateway
    """

    DEFAULT_HOST = 'localhost'
    DEFAULT_PORT = '9091'
    DEFAULT_JOB = 'weather'
    DEFAULT_INSTANCE = 'Vantage'
    DEFAULT_TIMEOUT = 10
    DEFAULT_MAX_TRIES = 3
    DEFAULT_RETRY_WAIT = 5

    def __init__(self, queue, manager_dict,
                 host=DEFAULT_HOST,
                 port=DEFAULT_PORT,
                 job=DEFAULT_JOB,
                 instance=DEFAULT_INSTANCE,
                 skip_post=False,
                 max_backlog=sys.maxsize,
                 stale=60,
                 log_success=True,
                 log_failure=True,
                 timeout=DEFAULT_TIMEOUT,
                 max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT):

        super(PromPushThread, self).__init__(
            queue,
            protocol_name='PromPush',
            manager_dict=manager_dict,
            max_backlog=max_backlog,
            stale=stale,
            log_success=log_success,
            log_failure=log_failure,
            timeout=timeout,
            max_tries=max_tries,
            retry_wait=retry_wait
        )

        self.host = host
        self.port = port
        self.job = job
        self.instance = instance
        self.skip_post = weeutil.weeutil.to_bool(skip_post)

    def post_metrics(self, data):
        # post the weather stats to the prometheus push gw
        pushgw_url = 'http://' + self.host + ":" + \
                     self.port + "/metrics/job/" + self.job

        if self.instance != "":
            pushgw_url += "/instance/" + self.instance

        try:
            _res = requests.post(url=pushgw_url,
                                 data=data,
                                 headers={'Content-Type': 'application/octet-stream'})
            logging.info("pushgw post return code - %s" % _res.status_code)
            if 200 <= _res.status_code <= 299:
                # success
                return
            else:
                # something went awry
                logging.error("pushgw post error: %s" % _res.text)
                return
        except requests.ConnectionError as e:
            logging.error("pushgw post error: %s" % e)

    def process_record(self, record, dbm):
        _ = dbm

        record_data = ''

        if self.skip_post:
            logging.info("-- prompush: skipping post")
        else:
            for key, val in record.items():
                if val is None:
                    val = 0.0

                if weather_metrics.get(key):
                    # annotate the submission with the appropriate metric type.
                    # if there's no metric type supplied the pushgw will
                    # annotate with 'untyped'
                    record_data += "# TYPE %s %s\n" % (
                        str(key), weather_metrics[key])

                record_data += "%s %s\n" % ("weather_" + str(key), str(val))

        self.post_metrics(record_data)


# ---------------------------------------------------------------------
# misc. logging functions
def logmsg(level, msg):
    syslog.syslog(level, 'prom-push: %s' % msg)


def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)


def loginfo(msg):
    logmsg(syslog.LOG_INFO, msg)


def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)
