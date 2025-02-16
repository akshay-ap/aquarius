#
# Copyright 2021 Ocean Protocol Foundation
# SPDX-License-Identifier: Apache-2.0
#

import configparser

from elasticsearch import Elasticsearch
from flask import jsonify
from flask_swagger import swagger
from flask_swagger_ui import get_swaggerui_blueprint
from pymongo import MongoClient

from aquarius.app.assets import assets
from aquarius.app.pools import pools
from aquarius.app.util import get_bool_env_value
from aquarius.config import Config
from aquarius.constants import BaseURLs, Metadata
from aquarius.events.events_monitor import EventsMonitor
from aquarius.events.util import setup_web3
from aquarius.myapp import app

config = Config(filename=app.config["CONFIG_FILE"])
aquarius_url = config.aquarius_url


def get_version():
    conf = configparser.ConfigParser()
    conf.read(".bumpversion.cfg")
    return conf["bumpversion"]["current_version"]


@app.route("/")
def version():
    info = dict()
    info["software"] = Metadata.TITLE
    info["version"] = get_version()
    info["plugin"] = config.module
    return jsonify(info)


@app.route("/health")
def health():
    return get_status()


@app.route("/spec")
def spec():
    swag = swagger(app)
    swag["info"]["version"] = get_version()
    swag["info"]["title"] = Metadata.TITLE
    swag["info"]["description"] = Metadata.DESCRIPTION + "`" + aquarius_url + "`."
    swag["info"]["connected"] = get_status()
    # swag['basePath'] = BaseURLs.BASE_AQUARIUS_URL
    return jsonify(swag)


# Call factory function to create our blueprint
swaggerui_blueprint = get_swaggerui_blueprint(
    BaseURLs.SWAGGER_URL,
    aquarius_url + "/spec",
    config={"app_name": "Test application"},  # Swagger UI config overrides
)

# Register blueprint at URL
app.register_blueprint(swaggerui_blueprint, url_prefix=BaseURLs.SWAGGER_URL)
app.register_blueprint(assets, url_prefix=BaseURLs.ASSETS_URL)
app.register_blueprint(pools, url_prefix=BaseURLs.POOLS_URL)


def get_status():
    if config.get("oceandb", "module") == "elasticsearch":
        if Elasticsearch(config.db_url).ping():
            return "Elasticsearch connected", 200
        else:
            return "Not connected to any database", 400
    elif config.get("oceandb", "module") == "mongodb":
        if (
            MongoClient(config.db_url)
            .get_database(config.get("oceandb", "db.name"))
            .command("ping")
        ):
            return "Mongodb connected", 200
        else:
            return "Not connected to any database", 400
    else:
        return "Not connected to any database", 400


# Start events monitoring if required
if get_bool_env_value("EVENTS_ALLOW", 0):
    config_file = app.config["CONFIG_FILE"]
    monitor = EventsMonitor(setup_web3(config_file), config_file)
    monitor.start_events_monitor()


if __name__ == "__main__":
    if isinstance(config.aquarius_url.split(":")[-1], int):
        app.run(
            host=config.aquarius_url.split(":")[1],
            port=config.aquarius_url.split(":")[-1],
        )
    else:
        app.run()
