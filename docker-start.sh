#!/bin/bash

HELP=0
SERVICE=0
EXTERNAL_IP=
EXTERNAL_PORT=


while getopts "hdi:p:" opt; do
  case $opt in
    h) HELP=1
    ;;
    d) SERVICE=1
    ;;
    i) EXTERNAL_IP="$OPTARG"
    ;;
    p) EXTERNAL_PORT="$OPTARG"
    ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1
    ;;
  esac
done

if [ $HELP -eq 1 ]; then
  echo
  echo "Usage:"
  echo "docker_start.sh -- Run with defaults in interactive mode"
  echo "docker_start.sh [params]"
  echo "-h      -- Show help"
  echo "-d      -- Run as a service"
  echo "-i IP   -- Specify an external IP address to use"
  echo "-p port -- Specify an external port to use"
  echo
  exit 0
fi

if [ ! -f .env ] || [ ! -d ~/.aws ]; then
  echo "Expected AWS settings not found. Please run the aws-setup script."
  exit 1
fi

if [ ! -f .custom_env ]; then
  cp ./config/custom_variables .custom_env
fi

source .env
source .custom_env

AWS_ACCESS_KEY_ID="$( /usr/bin/awk -F' = ' '$1 == "aws_access_key_id" {print $2}' ~/.aws/credentials )"
AWS_SECRET_ACCESS_KEY="$( /usr/bin/awk -F' = ' '$1 == "aws_secret_access_key" {print $2}' ~/.aws/credentials )"
AWS_DEFAULT_REGION="$( /usr/bin/awk -F' = ' '$1 == "region" {print $2}' ~/.aws/config )"

CONTAINER_NAME=alexa_chromecast
docker stop $CONTAINER_NAME 2>/dev/null
docker rm $CONTAINER_NAME 2>/dev/null

docker build -t alexa-skill-chromecast .

if [ $SERVICE -eq 1 ]; then
  OPTIONS='-d --restart always'
else
  OPTIONS='-it'
fi
docker run --network="host" \
 --name $CONTAINER_NAME \
 $OPTIONS \
 -e "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"\
 -e "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"\
 -e "AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION"\
 -e "AWS_SNS_TOPIC_ARN=$AWS_SNS_TOPIC_ARN"\
 -e "EXTERNAL_IP=$EXTERNAL_IP"\
 -e "EXTERNAL_PORT=$EXTERNAL_PORT"\
 -e "PLEX_IP_ADDRESS=$PLEX_IP_ADDRESS"\
 -e "PLEX_PORT=$PLEX_PORT"\
 -e "PLEX_TOKEN=$PLEX_TOKEN"\
 -e "PLEX_SUBTITLE_LANG=$PLEX_SUBTITLE_LANG"\
 -e "YOUTUBE_API_KEY=$YOUTUBE_API_KEY"\
 alexa-skill-chromecast

