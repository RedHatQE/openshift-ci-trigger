import json
import os
import re

import gitlab
import requests
import yaml
from flask import Flask, request


app = Flask("webhook_server")


# TODO: Fill all addons and jobs mapping
PRODUCTS_AND_JOBS_MAPPING = {
    "dbaas-operator": "periodic-ci-CSPI-QE-MSI-rdbaas-operator-addon-poc-tests"
}


class RepositoryNotFoundError(Exception):
    pass


def data_from_config():
    config_file = os.environ.get("OPENSHIFT_CI_TRIGGER_CONFIG", "/config/config.yaml")
    with open(config_file) as fd:
        return yaml.safe_load(fd)


def repo_data_from_config(repository_name):
    config = data_from_config()
    data = config["repositories"].get(repository_name)
    if not data:
        raise RepositoryNotFoundError(
            f"Repository {repository_name} not found in config file"
        )

    return data


def get_api(url, token):
    gitlab_api = gitlab.Gitlab(url=url, private_token=token, ssl_verify=False)
    gitlab_api.auth()
    return gitlab_api


def process_hook(api, data):
    object_attributes = data["object_attributes"]
    if object_attributes.get("action") == "merge":
        project = api.projects.get(data["project"]["id"])
        merge_request = project.mergerequests.get(object_attributes["iid"])
        for change in merge_request.changes().get("changes", []):
            changed_file = change.get("new_path")
            matches = re.match(
                r"addons/(?P<addon>.*)/addonimagesets/(production|stage)/.*.yaml",
                changed_file,
            )
            if matches:
                addon = matches.group("addon")
                job = PRODUCTS_AND_JOBS_MAPPING.get(addon)
                if job:
                    trigger_openshift_ci_job(job)
                else:
                    app.logger.info(f"No job found for addon: {addon}")


def trigger_openshift_ci_job(job):
    app.logger.info(f"Triggering openshift-ci job: {job}")
    config_data = data_from_config()
    res = requests.post(
        url=f"{config_data['trigger_url']}/{job}",
        headers={"Authorization": f"Bearer {config_data['trigger_token']}"},
        data='{"job_execution_type": "1"}',
    )
    res_dict = json.loads(res.text)
    if (not res.ok) or res_dict["job_status"] != "TRIGGERED":
        app.logger.error(
            f"Failed to trigger openshift-ci job: {job}, response: {res_dict}"
        )
    return res_dict


@app.route("/process", methods=["POST"])
def process():
    hook_data = request.json
    event_type = hook_data["event_type"]
    repository_name = hook_data["repository"]["name"]
    app.logger.info(f"{repository_name}: Event type: {event_type}")
    repository_data = repo_data_from_config(repository_name=repository_name)
    api = get_api(
        url=repository_data["gitlab_url"], token=repository_data["gitlab_token"]
    )
    process_hook(api=api, data=hook_data)
    return "Process done"


def main():
    app.logger.info("Starting openshift-ci-trigger app")
    app.run(port=5000, host="0.0.0.0", use_reloader=False)


if __name__ == "__main__":
    main()