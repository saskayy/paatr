import os
import tempfile
import zipfile
import yaml

from docker.errors import ImageNotFound, NotFound, BuildError
from fastapi import Request
from fastapi.responses import JSONResponse
from git import Repo
import json

from . import (APP_CONFIG_FILE, CONFIG_KEYS_X, CONFIG_KEYS, 
                CONFIG_VALUE_VALIDATOR, DOCKER_TEMPLATE, DOCKER_CLIENT)

async def handle_errors(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"message": str(exc)},
    )

async def save_file(filename, dir_path, contents, _mode="wb"):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    try:
        with open(os.path.join(dir_path, filename), _mode) as fp:
            fp.write(contents)
    except Exception as e:
        return False
    
    return True

async def get_app_config(config_path):
    with open(config_path, "r") as fp:
        config = yaml.safe_load(fp)
    
    if not config:
        return False, f"Invalid {APP_CONFIG_FILE} file"
    
    for key in CONFIG_KEYS_X:
        if key not in config:
            return False, f"Missing {key} key in {APP_CONFIG_FILE}"

        if not CONFIG_VALUE_VALIDATOR[key](config[key]):
            return False, f"Invalid value for {key} key in {APP_CONFIG_FILE}"

    for k,v in config.items():
        if k not in CONFIG_KEYS:
            return False, f"Invalid key {k} in {APP_CONFIG_FILE}"
        
        if k in CONFIG_KEYS_X:
            if not v:
                return False, f"Invalid value for {k} in {APP_CONFIG_FILE}"

    return True, config

def generate_docker_config(config):
    run = config.pop("run")
    if type(run) == list:
        run = " && ".join(run)

    return DOCKER_TEMPLATE.format(**config, run=f"RUN {run}", app_name=config["name"])

async def build_app(git_url, app_name):
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app_dir = os.path.join(tmp_dir, app_name)
            print(git_url)
            repo = Repo.clone_from(url=git_url, to_path=app_dir)

            files = os.listdir(app_dir)
            print(files)
            if APP_CONFIG_FILE not in files:
                return f"No {APP_CONFIG_FILE} file found in the app files"

            (is_valid, config) = await get_app_config(os.path.join(app_dir, APP_CONFIG_FILE))

            if not is_valid:
                return config
            config["name"] = app_name
            dockerfile = generate_docker_config(config)

            with open(os.path.join(tmp_dir, "dockerfile"), "w") as fp:
                fp.write(dockerfile)

            image, _ = await build_docker_image(tmp_dir, app_name)

    except BuildError as e:
        print("Hey something went wrong with image build!")
        for line in e.build_log:
            if 'stream' in line:
                print(line['stream'].strip())
        
    except Exception as e:
        raise e
        return "Failed to build app"
    
    return "App built successfully"

async def build_docker_image(app_dir, app_name):
    image, logs = DOCKER_CLIENT.images.build(path=app_dir, tag=app_name, rm=True)
    
    for line in logs:
        if "stream" in line:
            line_str = line["stream"].strip()
            if line_str.startswith("Step ") or line_str.startswith("--->"):
                continue
            if line_str.strip():
                print(line_str)
    else:
        print("Docker image build complete.")
    
    return image, logs

def get_image(app_name):
    try:
        return DOCKER_CLIENT.images.get(app_name)
    except ImageNotFound:
        return None

def get_container(app_name):
    try:
        return DOCKER_CLIENT.containers.get(app_name)
    except NotFound:
        return None

def stop_container(app_name):
    if cont := get_container(app_name):
        cont.stop()

def remove_container(app_name):
    if cont := get_container(app_name):
        cont.remove(force=True)

async def run_docker_image(app_name):
    if not get_image(app_name):
        return "App not found"
        
    stop_container(app_name)

    if cont := get_container(app_name):
        cont.start()
    else:
        (DOCKER_CLIENT.containers
                    .run(app_name, ports={'5000/tcp': 5000}, 
                            detach=True, name=app_name)
        )