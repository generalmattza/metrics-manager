#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Created By  : Matthew Davidson
# Created Date: 2024-02-20
# ---------------------------------------------------------------------------
"""An agent to manage a data pipeline.
It facilitates the acquisition of data from a variety of sources, processes it
and then store it in a database"""
# ---------------------------------------------------------------------------
from logging.config import dictConfig
import asyncio

from buffered import Buffer
from data_node_network.client import NodeClientUDP
from data_node_network.configuration import node_config
from metrics_processor import MetricsProcessor
from fast_database_clients.fast_influxdb_client import FastInfluxDBClient
from metrics_processor.pipeline import (
    JSONReader,
    Formatter,
    TimeLocalizer,
    FieldExpander,
    TimePrecision,
    OutlierRemover,
    PropertyMapper,
    FilterNone,
)
from metrics_processor import load_config
from network_simple import SimpleServerTCP
from html_scraper_agent import HTMLScraperAgent
from mqtt_node_network.metrics_gatherer import MQTTMetricsGatherer
from mqtt_node_network.configure import broker_config

def setup_logging(filepath="config/logger.yaml"):
    import yaml
    from pathlib import Path

    if Path(filepath).exists():
        with open(filepath, "r") as stream:
            config = yaml.load(stream, Loader=yaml.FullLoader)
    else:
        raise FileNotFoundError
    Path("logs/").mkdir(exist_ok=True)
    logger = dictConfig(config)
    return logger


def main():

    config = load_config("config/application.toml")

    processing_buffer = Buffer(maxlen=config["processor"]["input_buffer_length"])
    database_buffer = Buffer(maxlen=config["processor"]["output_buffer_length"])

    # TCP Server Configuration 
    # ************************************************************************
    # Create a TCP Server
    server_address = (
        config["server"]["host"],
        config["server"]["port"],
    )
    manager_server_tcp = SimpleServerTCP(
        output_buffer=processing_buffer,
        server_address=server_address,
    )

    # Node Client Configuration 
    # ************************************************************************
    # Create a client to gather data from the data nodes
    node_client = NodeClientUDP(nodes=node_config, buffer=processing_buffer)

    # Metrics Processor Configuration 
    # ************************************************************************
    # Create a metrics processor for the data pipeline
    metrics_processor = MetricsProcessor(
        input_buffer=processing_buffer,
        output_buffer=database_buffer,
        pipelines=[
            JSONReader,
            FilterNone,
            TimeLocalizer,
            TimePrecision,
            FieldExpander,
            Formatter,
            OutlierRemover,
            PropertyMapper,
        ],
        config=config,
    )

    # HTML Scraper Configuration 
    # ************************************************************************
    # Initialize html scraper
    scraper_agent = HTMLScraperAgent(metrics_processor.input_buffer)

    config_scraper = config["html_scraper_agent"]
    # # scraper_address = "config/test.html"

    # MQTT Client Configuration 
    # ************************************************************************
    client = (
        MQTTMetricsGatherer(
            broker_config=broker_config,
            node_id="metrics-manager-mqtt-client",
            buffer=metrics_processor.input_buffer,
        )
        .connect()
        .loop_start()
    )
    # client.subscribe(topic="node_0/metrics", qos=0)
    client.subscribe(topic="prototype-zero/#", qos=0)

    # InfluxDB Configuration 
    # ************************************************************************
    # Create a client to write metrics to an InfluxDB database
    database_client = FastInfluxDBClient.from_config_file(
        buffer=database_buffer, config_file=config["database_client"]["config_file"]
    )
    # Start periodic writing to the database
    database_client.start()

    # Gather and run async clients 
    # ************************************************************************
    async def gather_data_from_agents():
        await asyncio.gather(
            scraper_agent.do_work_periodically(
                update_interval=config_scraper["update_interval"],
                server_address=config_scraper["scrape_address"],
            ),
            node_client.periodic_request(message="get_data"),
        )

    asyncio.run(gather_data_from_agents())


if __name__ == "__main__":

    setup_logging()
    main()
