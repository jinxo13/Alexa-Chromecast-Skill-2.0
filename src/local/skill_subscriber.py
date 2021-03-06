import os
import sys
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from requests import get
import miniupnpc
import boto3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PING_SECS = 600
UPNP_DISCOVERY_DELAY = 10


class Subscriber(BaseHTTPRequestHandler):
    """
    Generic Skill Subscription class to handle commands from an
    Lambda Function via SNS notifications.
    """

    def __init__(self, skills, ip, port, topic_arn=os.getenv('AWS_SNS_TOPIC_ARN')):
        self.last_ping_sent = False
        self.last_ping_received: Optional[datetime] = None
        self.ping_thread = threading.Thread(target=self.ping)

        self.token = ""
        self.stopped = False
        if port:
            self.manual_port_forward = True
        else:
            self.manual_port_forward = False
            try:
                self.initialize_upnp()
            except:
                logger.exception(
                    'Failed to configure UPnP. Please map port manually and pass PORT environment variable.')
                sys.exit(1)

        self.sns_client = boto3.client('sns')
        self.skills = skills
        self.topic_arn = topic_arn
        instance = self

        """
        HTTP Server implementation - receives messages from SNS
        """

        class SNSRequestHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header('content-type', 'text/html')
                self.end_headers()
                raw_data = self.rfile.read(
                    int(self.headers['Content-Length']))
                data = json.loads(raw_data)
                header_topic_arn = self.headers.get('X-Amz-Sns-Topic-Arn')
                msg_type = data['Type']

                if msg_type == 'SubscriptionConfirmation':
                    logger.info('Received subscription confirmation...')
                    token = data['Token']
                    instance.confirm_subscription(header_topic_arn, token)

                elif msg_type == 'Notification' and data['Message']:
                    logger.info('Received message:')
                    logger.info(json.dumps(json.loads(data['Message']), indent=4, sort_keys=True))
                    instance.dispatch_notification(json.loads(data['Message']))

            def log_message(self, format, *args):
                # Left in case required for debugging
                pass

        self.server: HTTPServer = HTTPServer(('', int(port) if port else 0), SNSRequestHandler)

        port = self.server.server_port
        if not ip:
            ip = self.get_external_ip()
        self.endpoint_url = 'http://{}:{}'.format(ip, port)
        logger.info('Listening on {}'.format(self.endpoint_url))
        self.subscribe()

    def serve_forever(self):
        try:
            while not self.stopped:
                # No timeout - so blocks while waiting for a request
                self.server.handle_request()
        except:
            logger.exception('Unexpected error')

    def ping(self):
        """
        Sends a simple ping message to SNS
        """
        while not self.stopped:
            if not self.last_ping_sent or (datetime.now() - self.last_ping_sent).total_seconds() > PING_SECS:
                sns_client = boto3.client("sns")
                response = sns_client.list_subscriptions_by_topic(TopicArn=self.topic_arn)
                subscriptions = response['Subscriptions']
                if len(subscriptions) == 0:
                    logger.error('No clients are subscribed.')
                else:
                    logger.info('%i clients are subscribed.' % len(subscriptions))

                if (self.last_ping_received
                    and (datetime.now() - self.last_ping_received).total_seconds() > PING_SECS * 2):
                    logger.error(f'No ping received for {PING_SECS * 2} seconds. Restarting process...')
                    self.restart()

                logger.info('Sending ping...')
                self.sns_client.publish(TopicArn=self.topic_arn, Message=json.dumps({'command': 'ping'}))
                self.last_ping_sent = datetime.now()
            else:
                time.sleep(1)

    @staticmethod
    def restart():
        os.execl(sys.executable, sys.executable, *['-m', 'local.main'])

    def shutdown(self, signum, frame):
        """
        Performs a graceful shutdown stopping HTTP Server and Ping thread
        """
        if self.stopped: return
        self.stopped = True
        logger.info('Shutting down HTTP listener')
        self.unsubscribe()
        self.server.shutdown()
        self.ping_thread.join(5)

    def initialize_upnp(self):
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = UPNP_DISCOVERY_DELAY
        upnp.discover()
        upnp.selectigd()
        self.upnp = upnp

    def get_external_ip(self):
        return get('https://api.ipify.org').text

    def subscribe(self):
        """
        Subscribes to receive message from SNS for the specified topic.
        A subscription confirmation request should then be received from SNS.
        """
        if not self.manual_port_forward:
            try:
                self.upnp.addportmapping(
                    self.server.server_port,
                    'TCP',
                    self.upnp.lanaddr,
                    self.server.server_port,
                    '',
                    ''
                )
            except:
                logger.error('Failed to automatically forward port.')
                logger.error('Please set port as an environment variable and forward manually.')
                sys.exit(1)

        try:
            logger.info("Subscribing for Alexa commands...")
            self.sns_client.subscribe(
                TopicArn=self.topic_arn,
                Protocol='http',
                Endpoint=self.endpoint_url
            )

        except:
            logger.exception('SNS Topic ({}) is invalid. Please check in AWS.'.format(self.topic_arn))
            sys.exit(1)

    def confirm_subscription(self, topic_arn, token):
        """
        Confirms a subscription based on the received subscription confirmation request from sNS
        """

        try:
            self.sns_client.confirm_subscription(
                TopicArn=topic_arn,
                Token=token,
                AuthenticateOnUnsubscribe="false")
            logger.info('Subscribed.')

            # start ping
            self.ping_thread.start()

        except:
            logger.exception('Failed to confirm subscription. Please check in AWS.')
            sys.exit(1)

    def unsubscribe(self):
        """
        Unsubscribe from SNS Topic - stop receiving messages
        """

        if not self.manual_port_forward:
            result = self.upnp.deleteportmapping(self.server.server_port, 'TCP')

            if result:
                logger.debug('Removed forward for port {}.'.format(self.server.server_port))
            else:
                raise RuntimeError(
                    'Failed to remove port forward for {}.'.format(self.server.server_port))

        subscription_arn = None
        response = self.sns_client.list_subscriptions_by_topic(TopicArn=self.topic_arn)
        for sub in response['Subscriptions']:
            if sub['TopicArn'] == self.topic_arn and sub['Endpoint'] == self.endpoint_url:
                subscription_arn = sub['SubscriptionArn']
                break

        if subscription_arn is not None and subscription_arn[:12] == 'arn:aws:sns:':
            self.sns_client.unsubscribe(SubscriptionArn=subscription_arn)
        sys.exit(0)

    def dispatch_notification(self, notification):
        """
        Handle the notification
        """
        try:
            if notification['command'] == 'ping':
                logger.info('Received ping.')
                self.last_ping_received = datetime.now()
                return
            skill = self.skills.get(notification['handler_name'])
            skill.handle_command(notification['room'], notification['command'], notification['data'])
        except:
            logger.exception('Unexpected error handling message')
