# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# mypy: disable-error-code="union-attr"
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

import os
import json
from typing import Any, Dict, List, Optional

from github import Github, UnknownObjectException, GithubException

from .crew.crew import DevCrew
from agents.lead_developer_agent import LeadDeveloperAgent
# Assuming utils are in the same app directory
from .utils.github_integration import CodeFix, GitHubIntegration
from .utils.lighthouse import LighthouseConfig, LighthouseRunner

LOCATION = "global"
LLM = "gemini-2.0-flash-001"

# Instantiate the agents
lead_developer_agent_instance = LeadDeveloperAgent()
dev_crew_instance = DevCrew()

# Initialize Lighthouse runner
lighthouse_runner = LighthouseRunner(LighthouseConfig())

# Initialize GitHub integration if token is available
github_integration_instance: Optional[GitHubIntegration] = None
if github_token := os.getenv("GITHUB_TOKEN"):
    github_integration_instance = GitHubIntegration(github_token)

# Define tools for LeadDeveloperAgent methods
@tool
def design_high_level_architecture_tool(requirements: dict, project_scope: str) -> dict:
    """
    Use this tool to create a high-level system design and architecture.
    This tool calls the Lead Developer Agent to perform the design.
    Requires 'requirements' (dict from gather_requirements_tool) and 'project_scope' (str from define_project_scope_tool).
    """
    # Call the Lead Developer Agent to design the architecture.
    response = lead_developer_agent_instance.design_high_level_architecture(requirements, project_scope)
    
    # This tool returns the direct response from the agent.
    # The orchestrating agent can then inspect the 'status' and other details in the response.
    return response

@tool
def orchestrate_and_delegate_tasks_tool(architecture: dict, prioritized_tasks: list) -> dict:
    """
    Use this tool to break down the project into tasks and plan delegation.
    This tool calls the Lead Developer Agent to perform the task breakdown and delegation.
    Requires 'architecture' (dict from design_high_level_architecture_tool) and 'prioritized_tasks' (list from assess_priority_and_estimate_timeline_tool).
    """
    # The 'prioritized_tasks' from the planning agent is a dict with a 'prioritized_tasks' key which is a list.
    # We ensure the LLM passes the list itself.
    tasks_list = prioritized_tasks.get("prioritized_tasks", []) if isinstance(prioritized_tasks, dict) else prioritized_tasks
    
    # Call the Lead Developer Agent to orchestrate and delegate tasks.
    response = lead_developer_agent_instance.orchestrate_and_delegate_tasks(architecture, tasks_list)

    # This tool returns the direct response from the agent.
    # The orchestrating agent can then use this delegation plan for the next steps.
    return response

# --- GitHub Interaction Logic Functions ---
def _get_github_instance():
    """Initializes and returns a PyGithub instance using an environment variable for the token."""
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("GITHUB_TOKEN environment variable not set. GitHub interactions will fail.")
        return None
    return Github(github_token)

def _get_repo_details_logic(repo_identifier: str) -> dict:
    """
    Fetches details for a given GitHub repository.
    Args:
        repo_identifier: The full name of the repository (e.g., "owner/repo_name") 
                         or just "repo_name" (will default to authenticated user's repos).
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}

    try:
        repo = g.get_repo(repo_identifier)
        print(f"GitHub: Found existing repo: {repo.html_url}")
        todo_content = ""
        try:
            todo_file = repo.get_contents("TODO.md")
            todo_content = todo_file.decoded_content.decode("utf-8")
        except UnknownObjectException:
            print(f"GitHub: TODO.md not found in {repo.full_name}.")
        
        return {
            "repo_url": repo.html_url,
            "repo_full_name": repo.full_name,
            "status": "exists",
            "description": repo.description,
            "private": repo.private,
            "todo_md_content": todo_content,
            "message": f"Repository '{repo.full_name}' found."
        }
    except UnknownObjectException:
        print(f"GitHub: Repo '{repo_identifier}' not found.")
        return {"status": "not_found", "message": f"Repository '{repo_identifier}' not found."}
    except GithubException as e:
        print(f"GitHub: Error accessing repo '{repo_identifier}': {e}")
        return {"error": f"Failed to access repo: {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _create_repo_logic(
    repo_name: str,
    description: str = "",
    make_private: bool = True,
    auto_init: bool = True,
    license_template: str = "mit"
) -> dict:
    """
    Creates a new GitHub repository and initializes it.

    Args:
        repo_name: The name for the new repository (e.g., "my-new-project").
        description: Optional description for the repository.
        make_private: Whether the repository should be private.
        auto_init: If True, initializes the repository with a README.
        license_template: The license template to use (e.g., "mit", "gpl-3.0").
                          Only used if auto_init is True.
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}

    try:
        user = g.get_user()
        
        # Check if repo with this name already exists for the user
        try:
            user.get_repo(repo_name)
            print(f"GitHub: Repo '{repo_name}' already exists for user '{user.login}'. Cannot create duplicate.")
            return {
                "error": f"Repository '{repo_name}' already exists for you.",
                "status": "error_exists",
                "repo_full_name": f"{user.login}/{repo_name}"
            }
        except UnknownObjectException:
            # This is expected if the repo does not exist. Proceed to creation.
            pass

        # Create the repository with initialization options
        print(f"GitHub: Creating repo '{repo_name}' with auto_init={auto_init}...")
        repo = user.create_repo(
            repo_name,
            description=description,
            private=make_private,
            auto_init=auto_init,
            license_template=license_template
        )
        
        print(f"GitHub: Successfully created repo: {repo.html_url}")
        return {
            "repo_url": repo.html_url,
            "repo_full_name": repo.full_name,
            "status": "created",
            "message": f"Repository '{repo.full_name}' created successfully."
        }
    except GithubException as e:
        print(f"GitHub: Error creating repo '{repo_name}': {e}")
        error_message = f"Failed to create repo: {e.status} {getattr(e, 'data', str(e))}"
        if e.status == 422:
            error_message = f"Failed to create repo '{repo_name}'. It may already exist or the name is invalid. Details: {getattr(e, 'data', str(e))}"
        return {"error": error_message, "status": "error_api"}


def _update_repo_todo_logic(repo_full_name: str, task_summary: str, commit_message: str = "Update TODO.md") -> dict:
    """
    Creates or updates a TODO.md file in the given repo.
    Args:
        repo_full_name: The full name of the repository (e.g., "username/reponame").
        task_summary: The content to add to TODO.md.
        commit_message: The commit message for the change.
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}

    try:
        repo = g.get_repo(repo_full_name)
        file_path = "TODO.md"
        
        try:
            contents = repo.get_contents(file_path)
            # Append to existing TODO.md
            existing_content = contents.decoded_content.decode("utf-8")
            new_content = existing_content + "\n- " + task_summary
            repo.update_file(contents.path, commit_message, new_content, contents.sha)
            print(f"GitHub: Updated {file_path} in {repo.html_url}")
        except UnknownObjectException: # File does not exist
            new_content = f"# Project TODOs\n\n- {task_summary}"
            repo.create_file(file_path, commit_message, new_content)
            print(f"GitHub: Created {file_path} in {repo.html_url}")
        return {"todo_status": "updated/created", "todo_file_path": f"{repo.html_url}/blob/main/{file_path}"} # Assumes main branch
    except GithubException as e:
        print(f"GitHub: Error updating TODO.md: {e}")
        return {"error": f"Failed to update TODO.md: {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _get_file_content_logic(repo_full_name: str, file_path: str) -> dict:
    """
    Fetches the content of a specific file from a GitHub repository.
    Args:
        repo_full_name: The full name of the repository (e.g., "username/reponame").
        file_path: The path to the file within the repository (e.g., "src/main.py", "docs/README.md").
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}

    try:
        repo = g.get_repo(repo_full_name)
        file_contents = repo.get_contents(file_path)
        if isinstance(file_contents, list): # It's a directory
            return {"error": f"Path '{file_path}' is a directory, not a file.", "status": "error_path_is_directory"}
        
        decoded_content = file_contents.decoded_content.decode("utf-8")
        return {"repo_full_name": repo_full_name, "file_path": file_path, "content": decoded_content, "status": "success"}
    except UnknownObjectException:
        return {"error": f"File '{file_path}' not found in repository '{repo_full_name}'.", "status": "error_not_found"}
    except GithubException as e:
        return {"error": f"Error fetching file '{file_path}': {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _commit_file_changes_logic(repo_full_name: str, file_path: str, new_content: str, commit_message: str, branch: str = "main") -> dict:
    """
    Creates a new file or updates an existing file in the repository and commits the changes.
    Args:
        repo_full_name: The full name of the repository (e.g., "username/reponame").
        file_path: The path to the file within the repository (e.g., "src/main.py").
        new_content: The new content for the file.
        commit_message: The commit message.
        branch: The branch to commit to (defaults to "main").
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}
    try:
        repo = g.get_repo(repo_full_name)
        try:
            contents = repo.get_contents(file_path, ref=branch)
            repo.update_file(contents.path, commit_message, new_content, contents.sha, branch=branch)
            action = "updated"
        except UnknownObjectException: # File does not exist
            repo.create_file(file_path, commit_message, new_content, branch=branch)
            action = "created"
        return {"status": f"file_{action}", "file_path": file_path, "repo_full_name": repo_full_name, "branch": branch, "commit_message": commit_message}
    except GithubException as e:
        return {"error": f"Error committing file '{file_path}': {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _create_branch_logic(repo_full_name: str, new_branch_name: str, source_branch_name: str = "main") -> dict:
    """
    Creates a new branch in the repository from a source branch.
    Args:
        repo_full_name: The full name of the repository.
        new_branch_name: The name for the new branch.
        source_branch_name: The branch to create the new branch from (defaults to "main").
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}
    try:
        repo = g.get_repo(repo_full_name)
        source_branch = repo.get_branch(source_branch_name)
        repo.create_git_ref(ref=f"refs/heads/{new_branch_name}", sha=source_branch.commit.sha)
        return {"status": "branch_created", "repo_full_name": repo_full_name, "new_branch_name": new_branch_name, "source_branch_name": source_branch_name}
    except GithubException as e:
        return {"error": f"Error creating branch '{new_branch_name}': {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _open_pull_request_logic(repo_full_name: str, title: str, body: str, head_branch: str, base_branch: str = "main", reviewers: list = None) -> dict:
    """
    Opens a new pull request.
    Args:
        repo_full_name: The full name of the repository.
        title: The title of the pull request.
        body: The description of the pull request.
        head_branch: The name of the branch where your changes are implemented.
        base_branch: The name of the branch you want the changes pulled into (defaults to "main").
        reviewers: Optional list of GitHub usernames to request reviews from.
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}
    try:
        repo = g.get_repo(repo_full_name)
        pr = repo.create_pull(title=title, body=body, head=head_branch, base=base_branch)
        if reviewers:
            pr.create_review_request(reviewers=reviewers)
        return {"status": "pull_request_opened", "pr_number": pr.number, "pr_url": pr.html_url, "repo_full_name": repo_full_name}
    except GithubException as e:
        return {"error": f"Error opening pull request: {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

def _create_release_logic(repo_full_name: str, tag_name: str, release_name: str, release_message: str, target_commitish: str = "main", draft: bool = False, prerelease: bool = False) -> dict:
    """
    Creates a new release in the repository.
    """
    g = _get_github_instance()
    if not g:
        return {"error": "GitHub token not configured.", "status": "error_config"}
    try:
        repo = g.get_repo(repo_full_name)
        release = repo.create_git_release(tag=tag_name, name=release_name, message=release_message, target_commitish=target_commitish, draft=draft, prerelease=prerelease)
        return {"status": "release_created", "release_name": release.name, "tag_name": release.tag_name, "html_url": release.html_url}
    except GithubException as e:
        return {"error": f"Error creating release '{release_name}': {e.status} {getattr(e, 'data', str(e))}", "status": "error_api"}

# --- Planning Functions (moved from PlanningAgent) ---
def _gather_requirements_logic(initial_input: str) -> dict:
    """
    Processes initial user input to structure it as a preliminary requirement.
    """
    print(f"Planning: Processing initial input: {initial_input[:50]}...")
    return {
        "raw_input": initial_input,
        "processed_input_summary": f"Initial input received: '{initial_input}'. Further clarification and GitHub setup will be handled by the LLM."
    }
    
def _define_project_scope_logic(requirements: dict) -> str:
    """
    Defines the project scope based on the gathered requirements.
    """
    print(f"Planning: Defining project scope based on requirements: {str(requirements)[:100]}...")
    # Placeholder: Logic to synthesize requirements into a concise scope statement.
    # This could involve an LLM call to summarize or structure.
    feature_summary = requirements.get("key_features", ["unspecified features"])
    return f"Scope: Develop a system based on features: {', '.join(feature_summary)}."

def _assess_priority_and_estimate_timeline_logic(project_scope: str, requirements: dict) -> dict:
    """
    Assesses task priorities and provides a high-level timeline estimation.
    """
    print(f"Planning: Assessing priorities for scope: {project_scope[:100]}...")
    # Placeholder: Actual logic for prioritization (e.g., MoSCoW) and effort estimation.
    # This could involve heuristics or an LLM call.
    tasks = requirements.get("key_features", ["Core Development"])
    prioritized_tasks = [{"task_id": f"P00{i+1}", "description": task, "priority": "High"} for i, task in enumerate(tasks)]
    return {"prioritized_tasks": prioritized_tasks, "estimated_timeline": "TBD", "confidence_level": "Low"}

def _communicate_with_stakeholders_logic(update_message: str, stakeholders: list) -> dict:
    """
    Simulates communicating updates or queries to stakeholders.
    """
    print(f"Planning: Communicating to stakeholders ({', '.join(stakeholders)}): {update_message}")
    # Placeholder: Logic to send notifications (e.g., email, Slack).
    return {"status": "Communication sent", "recipients": stakeholders}

def _translate_context_logic(human_intent: str, technical_specs: dict) -> dict:
    """
    Simulates translating between human intent and technical specifications.
    """
    print("Planning: Translating context...")
    # Placeholder: Logic for translation.
    return {"human_readable_summary": human_intent, "technical_translation": "Technical details based on intent."}

# --- New Logic for Added Tools ---
def _code_analysis_logic(file_content: str) -> dict:
    """
    Performs static analysis on a file's content.
    This is a placeholder for a real linting/analysis tool.
    """
    print(f"Code Analysis: Analyzing file content...")
    # Placeholder logic
    lines = file_content.splitlines()
    complexity = len(lines) / 10  # Dummy complexity score
    errors = []
    if len(lines) > 300:
        errors.append("Warning: File is over 300 lines long, consider refactoring.")
    if "TODO" in file_content:
        errors.append("Info: Found 'TODO' comments in the code.")
    
    return {
        "status": "analysis_complete",
        "complexity_score": f"{complexity:.2f}",
        "linting_issues": errors if errors else "No major issues found.",
        "suggestions": ["Consider adding more detailed comments."]
    }

def _documentation_generation_logic(file_content: str, file_path: str) -> dict:
    """
    Generates documentation for a given code file.
    This is a placeholder for a more sophisticated documentation generator.
    """
    print(f"Documentation Agent: Generating docs for {file_path}...")
    # Placeholder logic
    doc = f"""
# Documentation for {file_path}

This document provides an overview of the code in `{file_path}`.

## Summary
The file contains {len(file_content.splitlines())} lines of code.
It appears to define functions/classes related to [ASSUMED_PURPOSE].

## Key Functions/Classes
- `function_or_class_1`: [Brief description]
- `function_or_class_2`: [Brief description]

*Note: This is auto-generated documentation.*
"""
    return {"status": "documentation_generated", "documentation": doc}

def _security_scan_logic(repo_full_name: str) -> dict:
    """
    Simulates a security scan on the repository.
    This would typically scan dependency files like requirements.txt.
    """
    print(f"Security Agent: Scanning {repo_full_name} for vulnerabilities...")
    # Placeholder: In a real scenario, this would read a dependency file and check against a database.
    return {
        "status": "scan_complete",
        "vulnerabilities_found": [
            {
                "dependency": "outdated-package==1.2.3",
                "severity": "High",
                "summary": "Remote Code Execution vulnerability.",
                "cve": "CVE-2024-XXXXX"
            }
        ],
        "recommendation": "Update to `outdated-package==1.2.4`."
    }

def _qa_test_generation_logic(file_content: str, function_name: str) -> dict:
    """
    Generates unit tests for a specific function within a code file.
    This is a placeholder for an AI-powered test generator.
    """
    print(f"QA Agent: Generating tests for function '{function_name}'...")
    # Placeholder logic
    test_code = f"""
import unittest

# Assuming the function '{function_name}' is in a file named 'source_file.py'
# from source_file import {function_name}

class Test{function_name.capitalize()}(unittest.TestCase):

    def test_nominal_case(self):
        # Test with typical inputs
        # self.assertEqual({function_name}(input1), expected_output1)
        self.assertTrue(True) # Placeholder assertion

    def test_edge_case(self):
        # Test with edge case inputs (e.g., None, empty strings, 0)
        # self.assertEqual({function_name}(input2), expected_output2)
        self.assertTrue(True) # Placeholder assertion

if __name__ == '__main__':
    unittest.main()
"""
    return {"status": "tests_generated", "test_suite_code": test_code, "function_tested": function_name}

# --- Tools ---
@tool
def get_github_repo_info_tool(repo_identifier: str) -> dict:
    """
    Fetches details for a given GitHub repository, including TODO.md content if it exists.
    Use this to check if a repository exists and get its current state.
    Args:
        repo_identifier: The full name of the repository (e.g., "owner/repo_name") or just "repo_name" 
                         (which defaults to the authenticated user's repositories).
    """
    return _get_repo_details_logic(repo_identifier)

@tool
def create_github_repository_tool(
    repo_name: str,
    description: str = "AI-generated project repository",
    make_private: bool = True,
    auto_init: bool = True,
    license_template: str = "mit"
) -> dict:
    """
    Creates a new GitHub repository for the authenticated user.

    To ensure the repository is not empty, this tool can initialize it
    with a README.md file and a LICENSE file upon creation. This avoids
    issues with adding files to an empty repository later.

    Args:
        repo_name: The name for the new repository (e.g., "my-new-project").
                   Must not contain spaces or slashes.
        description: Optional description for the repository.
        make_private: Whether the repository should be private (defaults to True).
        auto_init: If True, initializes the repository with a README.
                   This is highly recommended. Defaults to True.
        license_template: The key of the license template to use (e.g., "mit",
                          "gpl-3.0", "apache-2.0"). A LICENSE file is only
                          created if auto_init is also True. Defaults to "mit".
    """
    if "/" in repo_name or " " in repo_name:
        return {"error": "Invalid repository name. Name should not contain '/' or spaces.", "status": "error_validation"}
    
    # The underlying implementation of this function should be updated to pass
    # the new 'auto_init' and 'license_template' arguments to the GitHub API.
    return _create_repo_logic(
        repo_name,
        description,
        make_private,
        auto_init,
        license_template
    )

@tool
def lead_dev_commit_code_tool(repo_full_name: str, file_path: str, new_content: str, commit_message: str, branch: str = "main") -> dict:
    """
    Commits code changes using the Lead Developer Agent.
    """
    response = lead_developer_agent_instance.commit_code_changes(repo_full_name, file_path, new_content, commit_message, branch)
    if response["status"] == "approved_for_commit":
        # Commit the changes using the commit_file_changes_tool
        commit_result = _commit_file_changes_logic(
            repo_full_name=repo_full_name,
            file_path=file_path,
            new_content=new_content,  # Use the original new_content
            commit_message=response["commit_message"],  # Use the approved commit message
            branch=branch
        )
        return commit_result
    else:
        # Return the response from the LeadDeveloperAgent
        return response

@tool
def commit_file_changes_tool(repo_full_name: str, file_path: str, new_content: str, commit_message: str, branch: str = "main") -> dict:
    """
    Creates a new file or updates an existing file in the repository and commits the changes.
    Useful for committing code changes by development agents.
    Args:
        repo_full_name: The full name of the repository (e.g., "owner/repo_name").
        file_path: The path to the file within the repository (e.g., "src/main.py").
        new_content: The new content for the file.
        commit_message: The commit message for the change.
        branch: The branch to commit to (defaults to "main").
    """
    return _commit_file_changes_logic(repo_full_name, file_path, new_content, commit_message, branch)

@tool
def create_branch_tool(repo_full_name: str, new_branch_name: str, source_branch_name: str = "main") -> dict:
    """
    Creates a new branch in the repository from a source branch.
    Useful for Lead Developer or DevOps agents to manage development workflows.
    Args:
        repo_full_name: The full name of the repository.
        new_branch_name: The name for the new branch.
        source_branch_name: The branch to create the new branch from (defaults to "main").
    """
    return _create_branch_logic(repo_full_name, new_branch_name, source_branch_name)

@tool
def open_pull_request_tool(repo_full_name: str, title: str, body: str, head_branch: str, base_branch: str = "main", reviewers: list = None) -> dict:
    """
    Opens a new pull request. Can also be used to request reviews.
    Useful for Lead Developer or DevOps agents.
    Args:
        repo_full_name: The full name of the repository.
        title: The title of the pull request.
        body: The description of the pull request.
        head_branch: The name of the branch where your changes are implemented.
        base_branch: The name of the branch you want the changes pulled into (defaults to "main").
        reviewers: Optional list of GitHub usernames to request reviews from (e.g., ["user1", "user2"]).
    """
    return _open_pull_request_logic(repo_full_name, title, body, head_branch, base_branch, reviewers)

@tool
def get_github_file_content_tool(repo_full_name: str, file_path: str) -> dict:
    """
    Fetches the content of a specific file from a GitHub repository.
    Use this to read files, including those in subdirectories.
    Args:
        repo_full_name: The full name of the repository (e.g., "owner/repo_name").
        file_path: The path to the file within the repository (e.g., "src/main.py", "docs/folder/README.md").
    """
    return _get_file_content_logic(repo_full_name, file_path)

@tool
def gather_requirements_tool(initial_input: str) -> dict:
    """
    Use this tool to gather and clarify requirements from the user's initial input.
    This should be the first step in understanding a new request.
    """
    return _gather_requirements_logic(initial_input)

@tool
def update_github_todo_md_tool(repo_full_name: str, task_summary: str, commit_message: str = "Update TODO.md") -> dict:
    """
    Creates or updates a TODO.md file in the specified GitHub repository.
    Args:
        repo_full_name: The full name of the repository (e.g., "username/reponame").
        task_summary: The content to add as a new item in TODO.md.
        commit_message: The commit message for the change.
    """
    return _update_repo_todo_logic(repo_full_name, task_summary, commit_message)

@tool
def define_project_scope_tool(requirements: dict) -> str:
    """Use this tool to define the project scope based on gathered and clarified requirements."""
    return _define_project_scope_logic(requirements)

@tool
def assess_priority_and_estimate_timeline_tool(project_scope: str, requirements: dict) -> dict:
    """Use this tool to assess task priorities and estimate a timeline based on the defined project scope and requirements."""
    return _assess_priority_and_estimate_timeline_logic(project_scope, requirements)

@tool
def communicate_with_stakeholders_tool(update_message: str, stakeholders: list) -> dict:
    """
    Use this tool to communicate updates or queries to specified stakeholders.
    Provide the message and a list of stakeholder identifiers.
    """
    return _communicate_with_stakeholders_logic(update_message, stakeholders)

@tool
def translate_context_tool(human_intent: str, technical_specs: dict) -> dict:
    """
    Use this tool to translate between human-understandable intent and technical specifications,
    or vice-versa, to ensure clarity.
    """
    return _translate_context_logic(human_intent, technical_specs)

@tool
def create_release_tool(repo_full_name: str, tag_name: str, release_name: str, release_message: str, target_commitish: str = "main", draft: bool = False, prerelease: bool = False) -> dict:
    """
    Creates a new release in the repository.
    Useful for Lead Developer or DevOps agents for managing software releases.
    Args:
        repo_full_name: The full name of the repository.
        tag_name: The name of the tag for this release (e.g., "v1.0.0").
        release_name: The title of the release (e.g., "Version 1.0.0").
        release_message: The description or release notes for this release.
        target_commitish: Specifies the commitish value that determines where the Git tag is created from. Can be any branch or commit SHA (defaults to "main").
        draft: True to create a draft (unpublished) release, False to create a published one.
        prerelease: True to identify the release as a prerelease.
    """
    return _create_release_logic(repo_full_name, tag_name, release_name, release_message, target_commitish, draft, prerelease)

@tool
def coding_tool(code_instructions: str) -> str:
    """
    Use this tool to write a program given a detailed set of requirements, scope, architecture, task breakdown, and instructions.
    This tool calls the specialized development agent crew to write the code.
    This tool should be called AFTER all planning and architectural design steps are completed.
    """
    inputs = {"code_instructions": code_instructions}
    
    # Call the development crew to perform the coding task.
    response = dev_crew_instance.crew().kickoff(inputs=inputs)

    # The tool returns the crew's response directly, which should be the generated code or a status report.
    return response

# --- LIGHTHOUSE AUDIT TOOLS (FROM OLD AGENT) ---
@tool
def run_lighthouse_audit_tool(url: str, output_path: Optional[str] = None) -> str:
    """Run a Lighthouse audit on the specified URL.
    Args:
        url: The URL to audit.
        output_path: Optional path to save the report.
    Returns:
        JSON string containing audit results or an error message.
    """
    try:
        results = lighthouse_runner.run_audit(url, output_path)
        return json.dumps(results)
    except Exception as e:
        return f"Error running Lighthouse audit: {str(e)}"

@tool
def analyze_lighthouse_report_tool(report_json_string: str) -> str:
    """Analyze a Lighthouse report (JSON string) and extract key issues.
    Args:
        report_json_string: JSON string containing Lighthouse report.
    Returns:
        Formatted analysis of issues found or an error message.
    """
    try:
        report_data = json.loads(report_json_string)
        issues = lighthouse_runner.extract_issues(report_data)

        response_lines = ["# Lighthouse Audit Analysis\n"]
        if not issues:
            response_lines.append("No significant issues found or the report was empty/invalid.")
        
        for issue in issues:
            response_lines.extend([
                f"## {issue.title} (Impact: {issue.impact})",
                f"Score: {issue.score:.2f}" if issue.score is not None else "Score: N/A",
                f"\n{issue.description}\n",
            ])
            if issue.suggestions:
                response_lines.append("### Suggestions:")
                for suggestion in issue.suggestions:
                    response_lines.append(f"- {suggestion}")
            
            if issue.code_snippet:
                response_lines.extend([
                    "\n### Problematic Code Snippet (if available):",
                    "```", # Consider adding language hint if known e.g. ```html
                    issue.code_snippet,
                    "```\n"
                ])
        return "\n".join(response_lines)
    except json.JSONDecodeError:
        return "Error: Invalid JSON format for Lighthouse report."
    except Exception as e:
        return f"Error analyzing report: {str(e)}"

@tool
def create_github_pr_with_fixes_tool(repo_full_name: str, fixes: List[Dict[str, Any]], base_branch: str = "main") -> str:
    """Create a GitHub pull request with a list of code fixes.
    This tool will create a new branch (typically 'lighthouse-fixes'), commit all specified fixes, and then open a PR.
    Args:
        repo_full_name: Repository name in the format "owner/repo".
        fixes: A list of dictionaries, where each dictionary represents a fix and must contain:
               'file_path' (str): Path to the file to be modified.
               'original_content' (str): The original content of the file (for context or if needed by fix generation).
               'fixed_content' (str): The new, fixed content for the file.
               'description' (str): A brief description of the fix.
               'issue_title' (str): The title of the issue this fix addresses (e.g., Lighthouse audit item title).
        base_branch: The base branch into which the fixes should be merged (defaults to "main").
    Returns:
        URL of the created pull request or an error message.
    """
    if not github_integration_instance:
        return "GitHub integration not configured. Set GITHUB_TOKEN environment variable."
    try:
        code_fix_objects = [CodeFix(**fix_item) for fix_item in fixes]
        pr = github_integration_instance.apply_fixes(repo_full_name, code_fix_objects, base_branch)
        return f"Created pull request: {pr.html_url}"
    except Exception as e:
        return f"Error creating pull request with fixes: {str(e)}"

# --- NEW TOOLS ---
@tool
def code_analysis_tool(file_content: str) -> dict:
    """
    Use this tool to analyze a file's content for code quality, linting errors, and complexity.
    It should be used after new code is written and before it's committed to a feature branch.
    Args:
        file_content: The full content of the code file to be analyzed.
    """
    return _code_analysis_logic(file_content)

@tool
def documentation_generation_tool(file_content: str, file_path: str) -> dict:
    """
    Use this tool to generate documentation for a given code file.
    This can be called after code is considered stable. The output can be used to create or update READMEs or other documentation files.
    Args:
        file_content: The full content of the code file.
        file_path: The path of the file, to be included in the documentation.
    """
    return _documentation_generation_logic(file_content, file_path)

@tool
def security_scan_tool(repo_full_name: str) -> dict:
    """
    Use this tool to perform a basic security scan on a repository to check for known vulnerabilities in dependencies.
    It's a good practice to run this tool periodically or after adding new dependencies.
    Args:
        repo_full_name: The full name of the repository (e.g., "owner/repo_name").
    """
    return _security_scan_logic(repo_full_name)

@tool
def qa_test_generation_tool(file_content: str, function_name: str) -> dict:
    """
    Use this tool to generate unit tests for a specific function within a code file.
    This is useful for ensuring new code has adequate test coverage. The generated test code should then be saved to a new test file.
    Args:
        file_content: The full content of the code file containing the function.
        function_name: The name of the function to generate tests for.
    """
    return _qa_test_generation_logic(file_content, function_name)

# 2. Set up the language model
updated_tools = [
    # Existing Tools
    gather_requirements_tool,
    get_github_repo_info_tool,
    create_github_repository_tool,
    get_github_file_content_tool,
    update_github_todo_md_tool,
    commit_file_changes_tool,
    lead_dev_commit_code_tool,
    create_branch_tool,
    open_pull_request_tool,
    define_project_scope_tool,
    assess_priority_and_estimate_timeline_tool,
    design_high_level_architecture_tool,
    orchestrate_and_delegate_tasks_tool,
    communicate_with_stakeholders_tool,
    translate_context_tool,
    create_release_tool,
    coding_tool,
    # New Tools
    code_analysis_tool,
    documentation_generation_tool,
    security_scan_tool,
    qa_test_generation_tool,
    # --- ADDED LIGHTHOUSE TOOLS ---
    run_lighthouse_audit_tool,
    analyze_lighthouse_report_tool,
    create_github_pr_with_fixes_tool,
]
llm = ChatVertexAI(model=LLM, location=LOCATION, temperature=0, max_tokens=4096, streaming=True).bind_tools(updated_tools)



# 3. Define workflow components
def should_continue(state: MessagesState) -> str:
    """Determines whether to use the crew or end the conversation."""
    last_message = state["messages"][-1]
    return "tool_executor" if last_message.tool_calls else END


def call_model(state: MessagesState, config: RunnableConfig) -> dict[str, BaseMessage]:
    """Calls the language model and returns the response."""
    system_message = (
        "You are an AI agent in charge of multiple specialized agents with the Goal of helping a user develop software.\n"
        "Your primary role is to meticulously plan and oversee software development projects by interacting with the user.\n"
        "Follow these steps for project setup, planning, execution, and quality assurance:\n\n"

        "INITIAL INTERACTION & GITHUB SETUP:\n"
        "1. Start by greeting the user. Use 'gather_requirements_tool' on their first message to capture the initial project idea.\n"
        "2. Set up the GitHub repository. Ask the user for an existing repo or to create a new one. Use 'get_github_repo_info_tool' or 'create_github_repository_tool'. Handle any errors gracefully.\n"
        "3. Once the repo is confirmed, use 'update_github_todo_md_tool' to log the initial task.\n\n"

        "DETAILED PLANNING (after GitHub setup):\n"
        "4. Engage in a detailed conversation to clarify requirements.\n"
        "5. Use the planning tools in sequence: 'define_project_scope_tool', 'assess_priority_and_estimate_timeline_tool', 'design_high_level_architecture_tool', and 'orchestrate_and_delegate_tasks_tool'.\n\n"
        
        "CODING & DEVELOPMENT:\n"
        "6. Synthesize all planning information into detailed instructions for the coding phase.\n"
        "7. Use the 'coding_tool' with these instructions to get the code developed. The tool will return file paths and content.\n"
        "8. After receiving code from the 'coding_tool', use 'get_github_file_content_tool' to read the original file if it exists, to prepare for a review or merge.\n"
        "9. If the file did not exist use github tools to create the file\n\n"

        "QUALITY & REVIEW CYCLE (after code is generated):\n"
        "9. For each new or modified code file, you MUST use the quality tools:\n"
        "   a. Use 'code_analysis_tool' to check for linting issues and complexity.\n"
        "   b. Use 'qa_test_generation_tool' to create unit tests for key functions in the new code. You will need to commit these generated tests to the repository in a separate test file.\n"
        "10. After the code is analyzed and has tests, use 'lead_dev_commit_code_tool' to commit the application code and the new test files to a new feature branch (use 'create_branch_tool' first).\n"
        "11. Once committed, you can use other tools as needed:\n"
        "    - 'documentation_generation_tool' to create documentation for the new code.\n"
        "    - 'security_scan_tool' to check for dependency vulnerabilities.\n"
        "    - 'open_pull_request_tool' to create a PR for review.\n\n"

        "LIGHTHOUSE AUDITING & WEB PERFORMANCE (NEW CAPABILITIES):\n"
        "12. If the user requests a web performance audit or mentions Lighthouse, use the 'run_lighthouse_audit_tool' with the target URL.\n"
        "13. After obtaining the Lighthouse report (JSON string) from 'run_lighthouse_audit_tool', use 'analyze_lighthouse_report_tool' to get a human-readable analysis of issues.\n"
        "14. Present the analysis to the user. If fixes are identified and the user agrees, you can suggest preparing these fixes.\n"
        "15. To apply a batch of code fixes (e.g., from Lighthouse analysis or other sources) and create a Pull Request, use the 'create_github_pr_with_fixes_tool'. This tool requires a list of fix objects, each specifying file path, original/fixed content, description, and issue title. It will create a new branch, commit the fixes, and open a PR.\n\n"


        "GENERAL NOTES:\n"
        "- You must use tools in a logical sequence. Do not skip the quality assurance steps.\n"
        "- If the user provides new information, you may need to re-run planning tools.\n"
        "- Preserve any test cases mentioned by the user as they are valuable.\n"
    )


    messages_with_system = [{"type": "system", "content": system_message}] + state[
        "messages"
    ]
    # Forward the RunnableConfig object to ensure the agent is capable of streaming the response.
    response = llm.invoke(messages_with_system, config)
    return {"messages": response}


# 4. Create the workflow graph
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("tool_executor", ToolNode(updated_tools)) # Renamed node for clarity
workflow.set_entry_point("agent")

# 5. Define graph edges
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tool_executor", "agent")

# 6. Compile the workflow
agent = workflow.compile()
