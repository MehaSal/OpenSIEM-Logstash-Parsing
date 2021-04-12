#!/usr/bin/env python3
import datetime
import logging
import os
import time
import json
from logging.handlers import RotatingFileHandler

import requests
import sns
import secret
from kafka import KafkaProducer
import kafka_producer


logger = logging.getLogger()
logger.setLevel('INFO')
log_path = os.path.basename(__file__).split('.')[0] + '.log'

handler = RotatingFileHandler(
    log_path, maxBytes=1000000, backupCount=5)
formatter = logging.Formatter(
    "[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s")
handler.setLevel(logging.DEBUG)
handler.setFormatter(formatter)
logger.addHandler(handler)


def pull_okta_logs(minutes_before):
    logger.info('retrieving secrets for Okta')
    secrets = secret.get_secret('ngsiem-aca-logstash-api',
                                    ['okta_auth', 'sns_api_error_arn', 'okta_url'])
    current_time = datetime.datetime.utcnow()
    if minutes_before > 0:
        current_time = current_time - \
            datetime.timedelta(minutes=minutes_before)

    fifteen_minutes_ago = (current_time - datetime.timedelta(minutes=15)).isoformat()
    twenty_minutes_ago = (current_time - datetime.timedelta(minutes=20)).isoformat()

    url = f"{secrets['okta_url']}/api/v1/logs?since={twenty_minutes_ago}&until={fifteen_minutes_ago}"
    auth_token = f'SSWS {secrets["okta_auth"]}'
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'Authorization': auth_token}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
        else:
            logger.error(f"The API query for Okta is not returning a 200: {r.status_code}")
            sns.generate_sns("okta")
            return None

    except Exception as e:
        logger.error(f"Error occurred when querying for Okta logs: {e}")
        sns.generate_sns("okta")
        return None


if __name__ == "__main__":
    '''
    The okta API is JSON, and the date must be dynamically generated and in a special ISO format. The data output is
    small enough to be handled in an array and passed into the kafka producer function.
    '''

    minutes_before = 0 * 60
    minutes_before_file = os.path.join(os.getcwd(), 'minutes_before')
    if os.path.exists(minutes_before_file):
        with open(minutes_before_file, 'r') as minutes_file:
            line = minutes_file.readline()
            line = line.strip()
            minutes_before = int(line)
    while True:
        """
        Query Okta API (JSON format) starting from minutes_before
        send logs to kafka
        reduce minutes_before in next iteration and repeat
        when iteration reaches now -20 minutes
        run the job once every 5 minutes
        """
        logger.info(f'minutes before: {minutes_before}')
        if minutes_before <= 0:
            logger.info('waiting for 5 minutes')
            time.sleep(300)

        logger.info('okta query started')
        logs = pull_okta_logs(minutes_before)
        logger.info('okta query finished')
        minutes_before = minutes_before - 5

        if logs:
            logger.info('okta_produce started')
            kafka_producer.run_kafka_producer_job(logs, "log_audit_okta_monthly")
            logger.info('okta_produce finished')
        else:
            logger.info("No logs for Okta call.")
        with open(minutes_before_file, 'w') as minutes_file:
            minutes_before = 0 if minutes_before < 0 else minutes_before
            minutes_file.write(str(minutes_before))
