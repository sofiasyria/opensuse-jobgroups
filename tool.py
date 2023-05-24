#!/usr/bin/python3

import os
import sys
import re
import json
import yaml
import math
import argparse
import configparser
import subprocess
import functools
from textwrap import dedent

# Autoflush on print
print = functools.partial(print, flush=True)

def get_api_credentials():
	user_config = os.path.expanduser("~/.config/openqa/client.conf")
	system_config = "/etc/openqa/client.conf"
	if os.environ.get('APIKEY') and os.environ.get('APISECRET'):
		print("Getting APIKEY and APISECRET from environment.", file=sys.stderr)
		return (os.environ['APIKEY'], os.environ['APISECRET'])
	if os.path.exists(user_config):
		config = configparser.ConfigParser()
		config.read(user_config)
		if "openqa.opensuse.org" in config.sections():
			print("Getting APIKEY and APISECRET from '%s'" % user_config, file=sys.stderr)
			return (
				config.get("openqa.opensuse.org", "KEY"),
				config.get("openqa.opensuse.org", "SECRET")
			)
	if os.path.exists(system_config) and os.access(system_config, os.R_OK):
		config = configparser.ConfigParser()
		config.read(system_config)
		if "openqa.opensuse.org" in config.sections():
			print("Getting APIKEY and APISECRET from '%s'" % system_config, file=sys.stderr)
			return (
				config.get("openqa.opensuse.org", "KEY"),
				config.get("openqa.opensuse.org", "SECRET")
			)
	print("Error: Unable to get API credentials", file=sys.stderr)


api_credentials = None
def api_request(*args):
	global api_credentials
	if not api_credentials:
		api_credentials = get_api_credentials()
	APIKEY, APISECRET = api_credentials
	cmd = ['openqa-cli', 'api', '--o3', '-q']
	cmd.extend(['--apikey', APIKEY, '--apisecret', APISECRET])
	cmd.extend(args)
	try:
		output = subprocess.check_output(cmd)
	except subprocess.CalledProcessError as e:
		print("Error: API call %r returned exit code '%i'" % (args, e.returncode), file=sys.stderr)
		output = e.output
	try:
		j = json.loads(output)
	except json.decoder.JSONDecodeError:
		print("Error: openqa-cli returned: %s" % output)
		os._exit(1)
	return j


def github_workflow_encode(s):
	return s.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def show_server_error(r, filename=""):
	try:
		assert not isinstance(r['error'], str)
		for e in r['error']:
			if isinstance(e, dict):
				e = "  YAML Path: %(path)s\n  Message: %(message)s" % e
			emsg = "Error %i:\n%s" % (r['error_status'], e)
			if args.github:
				print("::error file=%s::%s" % (filename, github_workflow_encode(emsg)))
			else:
				print(emsg, file=sys.stderr)
	except:
		emsg = "Error %(error_status)i: %(error)s" % r
		if args.github:
			print("::error file=%s::%s" % (filename, github_workflow_encode(emsg)))
		else:
			print(emsg, file=sys.stderr)


def generate_header(filename):
	content = [
		"WARNING",
		"",
		"This file is managed in GIT!",
		"Any changes via the openQA WebUI will get overwritten!",
		"",
		"https://github.com/os-autoinst/opensuse-jobgroups",
		filename
	]
	line_length = max(max([len(line)+4 for line in content]), 58)
	content.insert(0, '#' * line_length)
	content.append('#' * line_length)
	def _align(line):
		prefix_len = math.floor((line_length - len(line)) / 2)
		suffix_len = math.ceil((line_length - len(line)) / 2)
		return ' ' * prefix_len + line + ' ' * suffix_len
	return "\n".join(map(lambda l: "#%s#" % l, map(_align, content)))


def normalize_jobgroup_filename(s):
	return s.strip().lower().\
		replace(' ', '_').\
		replace('(', '').replace(')', '').\
		replace(':', '').replace('/', '').\
		replace('_-_', '-').\
		replace(',', '').replace('+', '').\
		replace('__', '_').\
		replace('[deprecated]', 'DEPRECATED')


parser = argparse.ArgumentParser(
	description = 'Fetch and push jobgroups from and to openqa.opensuse.org',
	epilog = dedent("""
		You may provide tha API credentials via env vars (APIKEY/APISECRET),
		via ~/.config/openqa/client.conf or via /etc/openqa/client.conf.
	""")
)

group = parser.add_mutually_exclusive_group()
group.add_argument('--gendb', action='store_const', dest='action', const='gendb',
	help="Generate job_groups.yaml from O3"
)
group.add_argument('--orphans', action='store_const', dest='action', const='orphans',
	help="Check that every job group file has a corresponding entry in job_groups.yaml and serverside db"
)
group.add_argument('--headers', action='store_const', dest='action', const='headers',
	help="Check that every job group file has the correct header"
)
group.add_argument('--fetch', action='store_const', dest='action', const='fetch',
	help="Download all jobgroups as defined in job_groups.yaml"
)
group.add_argument('--push', action='store_const', dest='action', const='push',
	help="Upload all jobgroups as defined in job_groups.yaml"
)

parser.add_argument('--dry-run', action='store_true',
	help="Don't actually do anything. In push mode do serverside check via preview=1"
)
parser.add_argument('--github', action='store_true',
	help="Print parsable errors for Github CI (Github workflow commands)"
)
parser.add_argument('-j', dest='filter_job_group', type=int,
	help="""
		Perform fetch, push or gendb action only for this jobgroup id.
		For gendb this will append the respective entry.
	"""
)
parser.add_argument('-f', dest='filter_file_name', type=str,
	help="Perform fetch or push action only for this jobgroup yaml file"
)
args = parser.parse_args()


if not args.action:
	parser.print_help()
	os._exit(1)


# (try to) load job_groups.yaml
try:
	job_groups_db = yaml.safe_load(open('job_groups.yaml'))
except FileNotFoundError:
	job_groups_db = {}

# get everything we need from the api using only a single request
job_groups = sorted(api_request('job_groups'), key=lambda i: i['id'])



if args.action == 'gendb':
	if not args.filter_job_group:
		job_groups_db = {}
	yaml_line = None
	for job_group in job_groups:
		if args.filter_job_group and args.filter_job_group != job_group['id']:
			continue
		fs_name = normalize_jobgroup_filename(job_group['name'])
		if not args.filter_job_group:
			job_groups_db[job_group['id']] = fs_name
		yaml_line = yaml.safe_dump({job_group['id']: fs_name}).strip()
		print(yaml_line)
	if args.filter_job_group:
		if yaml_line and args.filter_job_group not in job_groups_db:
			with open('job_groups.yaml', 'a') as f:
				f.write(yaml_line)
				f.write("\n")
	elif not args.dry_run:
		yaml.safe_dump(job_groups_db, open('job_groups.yaml', 'w'))

elif args.action == 'fetch':
	job_groups_by_id = {}
	for job_group in job_groups:
		job_groups_by_id[job_group['id']] = job_group

	for gid, gname in job_groups_db.items():
		if args.filter_job_group and args.filter_job_group != gid:
			continue
		if args.filter_file_name and os.path.basename(args.filter_file_name) not in (gname, '%s.yaml' % gname):
			continue
		print("Fetching %i -> %s" % (gid, gname), file=sys.stderr)
		job_group = job_groups_by_id[gid]
		if not args.dry_run:
			template = job_group['template']
			filename = 'job_groups/%s.yaml' % gname
			header = generate_header(filename)
			if template == None:
				template = "---\nproducts: {}\nscenarios: {}\n"
			if not (template.startswith(header) or template.startswith("---\n"+header)):
				template = generate_header(filename) + "\n" + template
			open(filename, 'w').write(template)

elif args.action == 'push':
	job_groups_by_id = {}
	for job_group in job_groups:
		job_groups_by_id[job_group['id']] = job_group

	exit_code = 0
	for gid, gname in job_groups_db.items():
		if args.filter_job_group and args.filter_job_group != gid:
			continue
		if args.filter_file_name and os.path.basename(args.filter_file_name) not in (gname, '%s.yaml' % gname):
			continue
		job_group = job_groups_by_id[gid]
		if args.dry_run:
			print("Checking %s -> %i" % (gname, gid), file=sys.stderr)
			r = api_request('-X', 'POST', 'job_templates_scheduling/%i' % gid, 'schema=JobTemplates-01.yaml',
				'preview=1', '--param-file', 'template=job_groups/%s.yaml' % gname
			)
			if r.get('error'):
				show_server_error(r, 'job_groups/%s.yaml' % gname)
				exit_code = 1 # let's show the user all the error at once
			elif r.get('changes'):
				print('  ' + r['changes'].replace("\n", "\n  "), file=sys.stderr)
		else:
			print("Pushing %s -> %i" % (gname, gid), file=sys.stderr)
			r = api_request('-X', 'POST', 'job_templates_scheduling/%i' % gid, 'schema=JobTemplates-01.yaml',
				'preview=0', '--param-file', 'template=job_groups/%s.yaml' % gname
			)
			if r.get('error'):
				show_server_error(r, 'job_groups/%s.yaml' % gname)
				os._exit(1) # let's stop on the first error here
			elif r.get('changes'):
				print('  ' + r['changes'].replace("\n", "\n  "), file=sys.stderr)
	os._exit(exit_code)

elif args.action == 'orphans':
	# Check for job group files that are not referenced by job_groups.yaml
	job_groups_yaml = {'%s.yaml' % v: k for k, v in job_groups_db.items()}
	exit_code = 0
	for job_group_file in (f for f in os.listdir('job_groups') if f.endswith('.yaml')):
		if job_group_file not in job_groups_yaml:
			if args.github:
				print("::error file=job_groups/%s::Found orphaned file: job_groups/%s" % (job_group_file, job_group_file))
			else:
				print("Found orphaned file: job_groups/%s" % job_group_file, file=sys.stderr)
			exit_code = 1

	job_groups_by_id = {}
	for job_group in job_groups:
		job_groups_by_id[job_group['id']] = job_group
	for gid, gname in job_groups_db.items():
		# Check for job groups referenced by job_groups.yaml that do not exist on the server
		if not gid in job_groups_by_id:
			emsg = "Job group '%i' in job_groups.yaml doesn't exist on the server" % gid
			if args.github:
				print("::error file=job_groups.yaml::%s" % github_workflow_encode(emsg))
			else:
				print(emsg, file=sys.stderr)
			exit_code = 1
		jgfile = 'job_groups/%s.yaml' % gname
		# Check for job group files referenced by job_groups.yaml that do not exist in the repo
		if not os.path.exists(jgfile):
			emsg = "Job group file '%s' referenced by job_groups.yaml doesn't exist" % jgfile
			if args.github:
				print("::error file=job_groups.yaml::%s" % github_workflow_encode(emsg))
			else:
				print(emsg, file=sys.stderr)
			exit_code = 1
	os._exit(exit_code)

elif args.action == 'headers':
	exit_code = 0
	for job_group_file in (f for f in os.listdir('job_groups') if f.endswith('.yaml')):
		job_group_path = 'job_groups/%s' % job_group_file
		header = generate_header(job_group_path)
		with open(job_group_path) as f:
			text = f.read()
			if not (text.startswith(header) or text.startswith("---\n"+header)):
				emsg = "Job group '%s' doesn't have a valid header - expected:\n%s" % (job_group_path, header)
				if args.github:
					print("::error file=%s::%s" % (job_group_path, github_workflow_encode(emsg)))
				else:
					print(emsg, file=sys.stderr)
				exit_code = 1
			if len(re.findall("This file is managed in GIT!", text)) > 1:
				emsg = "Job group '%s' has multiple headers" % job_group_path
				if args.github:
					print("::error file=%s::%s" % (job_group_path, github_workflow_encode(emsg)))
				else:
					print(emsg, file=sys.stderr)
				exit_code = 1
	os._exit(exit_code)
