"""
We love you, Steve Jobs.
"""
import logging
from typing import List, Optional, Tuple, Dict, Type

from ogr.abstract import GitProject, GitService
from ogr.services.github import GithubProject

from packit.ogr_services import GithubService, PagureService
from packit.api import PackitAPI
from packit.config import JobConfig, JobTriggerType, JobType, PackageConfig, Config
from packit.config import get_packit_config_from_repo
from packit.distgit import DistGit
from packit.exceptions import PackitException
from packit.local_project import LocalProject
from packit.utils import nested_get, get_namespace_and_repo_name

logger = logging.getLogger(__name__)


JOB_NAME_HANDLER_MAPPING: Dict[JobType, Type["JobHandler"]] = {}


def add_to_mapping(kls: Type["JobHandler"]):
    JOB_NAME_HANDLER_MAPPING[kls.name] = kls
    return kls


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self, config: Config):
        self.config = config
        self._github_service = None
        self._pagure_service = None

    @property
    def github_service(self):
        if self._github_service is None:
            self._github_service = GithubService(
                token=self.config.github_token, read_only=self.config.dry_run
            )
        return self._github_service

    @property
    def pagure_service(self):
        if self._pagure_service is None:
            self._pagure_service = PagureService(
                token=self.config.pagure_user_token,
                read_only=self.config.dry_run,
                # TODO: how do we change to stg here? ideally in self.config
            )
        return self._pagure_service

    def get_job_input_from_github_release(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """
        look into the provided event and see if it's one for a published github release;
        if it is, process it and return input for the job handler
        """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        release = nested_get(event, "release")
        if action == "published" and release:
            repo_namespace = nested_get(event, "repository", "owner", "login")
            repo_name = nested_get(event, "repository", "name")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            release_ref = nested_get(event, "release", "tag_name")
            if not release_ref:
                logger.warning("Release tag name is not set.")
                return None
            logger.info(
                f"New release event {release_ref} for repo {repo_namespace}/{repo_name}."
            )
            gh_proj = GithubProject(
                repo=repo_name, namespace=repo_namespace, service=self.github_service
            )
            package_config = get_packit_config_from_repo(gh_proj, release_ref)
            https_url = event["repository"]["html_url"]
            package_config.upstream_project_url = https_url
            return JobTriggerType.release, package_config, gh_proj
        return None

    def get_job_input_from_github_pr(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """ look into the provided event and see if it's one for a new github pr """
        action = nested_get(event, "action")
        logger.debug(f"action = {action}")
        pr_id = nested_get(event, "number")
        is_pr = nested_get(event, "pull_request")
        if not is_pr:
            logger.info("Not a pull request event.")
            return None
        if action in ["opened", "reopened", "synchronize"] and pr_id:
            repo_namespace = nested_get(
                event, "pull_request", "head", "repo", "owner", "login"
            )
            repo_name = nested_get(event, "pull_request", "head", "repo", "name")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            ref = nested_get(event, "pull_request", "head", "ref")
            if not ref:
                logger.warning("Ref where the PR is coming from is not set.")
                return None
            target_repo = nested_get(event, "repository", "full_name")
            logger.info(f"GitHub pull request {pr_id} event for repo {target_repo}.")
            gh_proj = GithubProject(
                repo=repo_name, namespace=repo_namespace, service=self.github_service
            )
            package_config = get_packit_config_from_repo(gh_proj, ref)
            https_url = event["repository"]["html_url"]
            package_config.upstream_project_url = https_url
            return JobTriggerType.pull_request, package_config, gh_proj
        return None

    def get_job_input_from_dist_git_commit(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """ this corresponds to dist-git event when someone pushes new commits """
        topic = nested_get(event, "topic")
        logger.debug(f"topic = {topic}")
        if topic == NewDistGitCommit.topic:
            repo_namespace = nested_get(event, "msg", "commit", "namespace")
            repo_name = nested_get(event, "msg", "commit", "repo")
            ref = nested_get(event, "msg", "commit", "branch")
            if not (repo_namespace and repo_name):
                logger.warning(
                    "We could not figure out the full name of the repository."
                )
                return None
            if not ref:
                logger.warning("Target branch for the new commits is not set.")
                return None
            logger.info(
                f"New commits added to dist-git repo {repo_namespace}/{repo_name}, branch {ref}."
            )
            msg_id = nested_get(event, "msg_id")
            logger.info(f"msg_id = {msg_id}")
            dg_proj = self.pagure_service.get_project(
                repo=repo_name, namespace=repo_namespace
            )
            package_config = get_packit_config_from_repo(dg_proj, ref)
            return JobTriggerType.commit, package_config, dg_proj
        return None

    def parse_event(
        self, event: dict
    ) -> Optional[Tuple[JobTriggerType, PackageConfig, GitProject]]:
        """
        When a new event arrives, we need to figure out if we are able to process it.

        :param event: webhook payload or fedmsg
        """
        if event:
            # Once we'll start processing multiple events from different sources,
            # we should probably break this method down and move it to handlers or JobTrigger

            # github webhooks
            response = self.get_job_input_from_github_release(event)
            if response:
                return response
            response = self.get_job_input_from_github_pr(event)
            if response:
                return response
            # fedmsg
            response = self.get_job_input_from_dist_git_commit(event)
            if response:
                return response
        return None

    def process_jobs(
        self,
        trigger: JobTriggerType,
        package_config: PackageConfig,
        event: dict,
        project: GitProject,
    ):

        for job in package_config.jobs:
            if trigger == job.trigger:
                handler_kls = JOB_NAME_HANDLER_MAPPING.get(job.job, None)
                if not handler_kls:
                    logger.warning(f"There is no handler for job {job}")
                    continue
                handler = handler_kls(
                    self.config,
                    package_config,
                    event,
                    project,
                    self.pagure_service,
                    self.github_service,
                    job,
                    trigger,
                )
                handler.run()

    def process_message(self, event: dict, topic: str = None):
        """
        this is the entrypoint to processing messages

        topic is meant to be a fedmsg topic for the message
        """
        if topic:
            # let's pre-filter messages: we don't need to get debug logs from processing
            # messages when we know beforehand that we are not interested in messages for such topic
            topics = [
                getattr(h, "topic", None) for h in JOB_NAME_HANDLER_MAPPING.values()
            ]
            if topic not in topics:
                return
        response = self.parse_event(event)
        if not response:
            logger.debug("We don't process this event")
            return
        trigger, package_config, project = response
        if not all([trigger, package_config, project]):
            logger.debug("This project is not using packit.")
            return
        self.process_jobs(trigger, package_config, event, project)


class JobHandler:
    """ Generic interface to handle different type of inputs """

    name: JobType
    triggers: List[JobTriggerType]

    def __init__(
        self,
        config: Config,
        package_config: PackageConfig,
        event: dict,
        project: GitProject,
        distgit_service: GitService,
        upstream_service: GitService,
        job: JobConfig,
        triggered_by: JobTriggerType,
    ):
        self.config: Config = config
        self.project: GitProject = project
        self.distgit_service: GitService = distgit_service
        self.upstream_service: GitService = upstream_service
        self.package_config: PackageConfig = package_config
        self.event: dict = event
        self.job: JobConfig = job
        self.triggered_by: JobTriggerType = triggered_by

    def run(self):
        raise NotImplementedError("This should have been implemented.")


class FedmsgHandler(JobHandler):
    """ Handlers for events from fedmsg """

    topic: str


@add_to_mapping
class NewDistGitCommit(FedmsgHandler):
    """ A new flag was added to a dist-git pull request """

    topic = "org.fedoraproject.prod.git.receive"
    name = JobType.sync_from_downstream
    triggers = [JobTriggerType.commit]

    def run(self):
        # rev is a commit
        # we use branch on purpose so we get the latest thing
        # TODO: check if rev is HEAD on {branch}, warn then?
        branch = nested_get(self.event, "msg", "commit", "branch")

        # self.project is dist-git, we need to get upstream

        dg = DistGit(self.config, self.package_config)
        self.package_config.upstream_project_url = (
            dg.get_project_url_from_distgit_spec()
        )

        if not self.package_config.upstream_project_url:
            raise PackitException(
                "URL in specfile is not set. We don't know where the upstream project lives."
            )

        n, r = get_namespace_and_repo_name(self.package_config.upstream_project_url)
        up = self.upstream_service.get_project(repo=r, namespace=n)
        lp = LocalProject(git_project=up)

        api = PackitAPI(self.config, self.package_config, lp)
        api.sync_from_downstream(
            dist_git_branch=branch,
            upstream_branch="master",  # TODO: this should be configurable
        )


# @add_to_mapping
# class CoprBuildFinished(FedmsgHandler):
#     topic="org.fedoraproject.prod.copr.build.end"
#     name = JobType.ReportCoprResult
#
#     def run(self):
#         msg = f"Build {self.event['msg']['build']} " \
#               f"{'passed' if self.event['msg']['status'] else 'failed'}.\n" \
#               f"\tpackage: {self.event['msg']['pkg']}\n" \
#               f"\tchroot: {self.event['msg']['chroot']}\n"
#         # TODO: lookup specific commit related to the build and comment on it
#         # local cache containing "watched" copr builds?

# class NewDistGitPRFlag(FedmsgHandler):
#     """ A new flag was added to a dist-git pull request """
#     topic = "org.fedoraproject.prod.pagure.pull-request.flag.added"
#     name = "?"
#
#     def run(self):
#         repo_name = self.event["msg"]["pull_request"]["project"]["name"]
#         namespace = self.event["msg"]["pull_request"]["project"]["namespace"]
#         pr_id = self.event["msg"]["pull_request"]["id"]
#
#         pull_request = pagure_repo.get_pr_info(pr_id=pr_id)


@add_to_mapping
class GithubPullRequestHandler(JobHandler):
    name = JobType.check_downstream
    triggers = [JobTriggerType.pull_request]
    # https://developer.github.com/v3/activity/events/types/#events-api-payload-28

    def run(self):
        pr_id = self.event["pull_request"]["number"]

        local_project = LocalProject(git_project=self.project)

        api = PackitAPI(self.config, self.package_config, local_project)

        api.sync_pr(
            pr_id=pr_id,
            dist_git_branch=self.job.metadata.get("dist-git-branch", "master"),
            # TODO: figure out top upstream commit for source-git here
        )


@add_to_mapping
class GithubReleaseHandler(JobHandler):
    name = JobType.propose_downstream
    triggers = [JobTriggerType.release]

    def run(self):
        """
        Sync the upstream release to dist-git as a pull request.
        """
        version = self.event["release"]["tag_name"]

        local_project = LocalProject(git_project=self.project)

        api = PackitAPI(self.config, self.package_config, local_project)

        api.sync_release(
            dist_git_branch=self.job.metadata.get("dist-git-branch", "master"),
            version=version,
        )


@add_to_mapping
class GithubCoprBuildHandler(JobHandler):
    name = JobType.copr_build
    triggers = [JobTriggerType.pull_request, JobTriggerType.release]

    def handle_release(self):
        if not self.job.metadata.get("targets"):
            logger.error(
                "'targets' value is required in packit config for copr_build job"
            )
        clone_url = self.event["repository"]["clone_url"]
        tag_name = self.event["release"]["tag_name"]

        local_project = LocalProject(git_project=self.project)
        api = PackitAPI(self.config, self.package_config, local_project)

        build_id, repo_url = api.run_copr_build(
            owner=self.job.metadata.get("owner") or "packit",
            project=self.job.metadata.get("project")
            or f"{self.project.namespace}-{self.project.repo}",
            committish=tag_name,
            clone_url=clone_url,
            chroots=self.job.metadata.get("targets"),
        )

        # report
        msg = f"Copr build(ID {build_id}) triggered\nMore info: {repo_url}"
        self.project.commit_comment(
            commit=self.project.get_sha_from_tag(tag_name), body=msg
        )

    def handle_pull_request(self):
        if not self.job.metadata.get("targets"):
            logger.error(
                "'targets' value is required in packit config for copr_build job"
            )
        clone_url = nested_get(self.event, "pull_request", "head", "repo", "clone_url")
        committish = nested_get(self.event, "pull_request", "head", "sha")

        local_project = LocalProject(git_project=self.project)
        api = PackitAPI(self.config, self.package_config, local_project)

        build_id, repo_url = api.run_copr_build(
            owner=self.job.metadata.get("owner") or "packit",
            project=self.job.metadata.get("project")
            or f"{self.project.namespace}-{self.project.repo}",
            committish=committish,
            clone_url=clone_url,
            chroots=self.job.metadata.get("targets"),
        )

        # report
        msg = f"Triggered copr build (ID:{build_id}).\nMore info: {repo_url}"
        logger.info(msg)

        target_repo_name = nested_get(
            self.event, "pull_request", "base", "repo", "name"
        )
        target_repo_namespace = nested_get(
            self.event, "pull_request", "base", "repo", "owner", "login"
        )
        pr_target_project = GithubProject(
            repo=target_repo_name,
            namespace=target_repo_namespace,
            service=GithubService(token=self.config.github_token),
        )
        pr_target_project.pr_comment(self.event["number"], msg)

    def run(self):
        if self.triggered_by == JobTriggerType.pull_request:
            self.handle_pull_request()
        elif self.triggered_by == JobTriggerType.release:
            self.handle_release()