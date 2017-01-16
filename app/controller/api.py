# -*- coding: utf-8 -*-

"""
    controller.api
    ~~~~~~~~~~~~~~

    External API interface implementation
    :doc:       http://cobra-docs.readthedocs.io/en/latest/API

    :author:    Feei <wufeifei#wufeifei.com>
    :homepage:  https://github.com/wufeifei/cobra
    :license:   MIT, see LICENSE for more details.
    :copyright: Copyright (c) 2016 Feei. All rights reserved
"""
import os
import logging
import traceback
from utils import config, common
from flask import request, jsonify
from werkzeug.utils import secure_filename
from app import web, db, CobraResults, CobraRules, CobraProjects, CobraVuls, CobraAuth, CobraTaskInfo
from engine import scan

logging = logging.getLogger(__name__)

# API base path
API_URL = '/api'


@web.route(API_URL + '/add', methods=['POST'])
def add_task():
    """
    Create a scan task
    post json to http://url/api/add_new_task
    example:
        {
            "key": "34b9a295d037d47eec3952e9dcdb6b2b",              // must, client key
            "target": "https://gitlab.com/username/project.git",    // must, gitlab address
            "branch": "master",                                     // must, the project branch
            "old_version": "old version here",                      // optional, if you choice diff scan mode, you should provide old version hash.
            "new_version": "new version here",                      // optional, if you choice diff scan mode, you should provide new version hash.
        }
    :return:
        The return value also in json format, usually is:
        {"code": 1001, "result": "error reason or success."}
        code: 1005: Unknown Protocol
        code: 1004: Unknown error, if you see this error code, most time is cobra's database error.
        code: 1003: You support the parameters is not json.
        code: 1002: Some parameters is empty. More information in "msg".
        code: 1001: Success, no error.
    """
    data = request.json
    if not data or data == "":
        return jsonify(code=1003, result=u'Only support json, please post json data.')

    key = data.get('key')

    auth = CobraAuth.query.filter_by(key=key).first()
    if auth is None:
        return jsonify(code=4002, result=u'Key verify failed')
    target = data.get('target')
    branch = data.get('branch')
    new_version = data.get('new_version', '')
    old_version = data.get('old_version', '')

    # one-click scan for manage projects
    project_id = data.get('project_id')
    if project_id is not None:
        project = CobraProjects.query.filter_by(id=project_id).first()
        if not project:
            return jsonify(code=1002, result=u'not find the project.')
        target = project.repository
        branch = 'master'
        new_version = ""
        old_version = ""

    # verify key
    if not key or key == "":
        return jsonify(code=1002, result=u'key can not be empty.')
    if not target or target == "":
        return jsonify(code=1002, result=u'url can not be empty.')
    if not branch or branch == "":
        return jsonify(code=1002, result=u'branch can not be empty.')

    code, result = scan.Scan(target).version(branch, new_version, old_version)
    return jsonify(code=code, result=result)


@web.route(API_URL + '/status', methods=['POST'])
def status_task():
    """
    Query the scan task status
    :return:
    """
    scan_id = request.json.get('scan_id')
    key = request.json.get('key')
    auth = CobraAuth.query.filter_by(key=key).first()
    if auth is None:
        return jsonify(code=4002, result=u'Key verify failed')
    c = CobraTaskInfo.query.filter_by(id=scan_id).first()
    if not c:
        return jsonify(status=4004)
    status = {
        0: 'init',
        1: 'scanning',
        2: 'done',
        3: 'error'
    }
    status_text = status[c.status]
    domain = config.Config('cobra', 'domain').value
    # project_id
    project_info = CobraProjects.query.filter_by(repository=c.target).first()
    if project_info:
        report = 'http://' + domain + '/report/' + str(project_info.id)
    else:
        report = 'http://' + domain
    result = {
        'status': status_text,
        'text': 'Success',
        'report': report,
        'allow_deploy': True
    }
    return jsonify(status=1001, result=result)


@web.route(API_URL + '/upload', methods=['POST'])
def upload_file():
    """
    Scan by uploading compressed files
    :return:
    """
    if 'file' not in request.files:
        return jsonify(code=1002, result="File can't empty!")
    file_instance = request.files['file']
    if file_instance.filename == '':
        return jsonify(code=1002, result="File name can't empty!")
    if file_instance and common.allowed_file(file_instance.filename):
        filename = secure_filename(file_instance.filename)
        file_instance.save(os.path.join(os.path.join(config.Config('upload', 'directory').value, 'uploads'), filename))
        # Start scan
        code, result = scan.Scan(filename).compress()
        return jsonify(code=code, result=result)
    else:
        return jsonify(code=1002, result="This extension can't support!")


@web.route(API_URL + '/queue', methods=['POST'])
def queue():
    from utils.queue import Queue
    """
    Pushed to a third-party vulnerability management platform
    Start the queue first
        celery -A daemon worker --loglevel=info

    :return:
    """
    # Configure the project ID and the vulnerability ID
    project_id = request.json.get('project_id')
    rule_id = request.json.get('rule_id')
    if project_id is None or rule_id is None:
        return jsonify(code=1002, result='Project ID and Rule ID can\'t empty!')

    # Project Info
    project_info = CobraProjects.query.filter_by(id=project_id).first()

    # Unpunched vulnerability and rule information
    result_all = db.session().query(CobraRules, CobraResults).join(CobraResults, CobraResults.rule_id == CobraRules.id).filter(
        CobraResults.project_id == project_id,
        CobraResults.status == 0,
        CobraResults.rule_id == rule_id
    ).all()

    if len(result_all) == 0:
        return jsonify(code=1001, result="There are no unpacked vulnerabilities")

    # Dealing with vulnerabilities
    for index, (rule, result) in enumerate(result_all):
        try:
            # Query the vulnerability type information
            vul_info = CobraVuls.query.filter(CobraVuls.id == rule.vul_id).first()
            # Pushed to a third-party vulnerability management platform
            q = Queue(project_info.name, vul_info.name, vul_info.third_v_id, result.file, result.line, result.code, result.id)
            q.push()
        except:
            print(traceback.print_exc())
    return jsonify(code=1001, result="Successfully pushed {0} vulnerabilities to a third-party vulnerability management platform".format(len(result_all)))
