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

import re
import ast

class CodeAuditAgent:
    """
    The Code Audit Agent is responsible for analyzing code quality,
    generating documentation, scanning for security vulnerabilities, and creating QA tests.
    This version uses more advanced techniques for a more thorough audit.
    """
    def __init__(self):
        self.role = "Code quality, documentation, security, and QA"
        # A mock database of known vulnerabilities. In a real-world scenario, this would be a comprehensive, updated database.
        self.vulnerability_db = {
            "outdated-package": {"version": "1.2.3", "severity": "High", "cve": "CVE-2024-12345", "summary": "Remote Code Execution"},
            "insecure-lib": {"version": "2.1.0", "severity": "Medium", "cve": "CVE-2024-67890", "summary": "Cross-Site Scripting (XSS)"},
            "django": {"version": "3.2.1", "severity": "Low", "cve": "CVE-2024-11111", "summary": "Denial of Service possibility"}
        }
        print(f"Initialized Code Audit Agent: {self.role}")

    def analyze_code_quality(self, file_content: str) -> dict:
        """
        Performs static analysis on a file's content using Abstract Syntax Trees (AST)
        to check for common quality issues like cyclomatic complexity and missing docstrings.
        
        Args:
            file_content: The string content of the file to analyze.
        
        Returns:
            A dictionary containing the analysis results.
        """
        print(f"CodeAuditAgent: Analyzing file for code quality using AST...")
        issues = []
        line_count = len(file_content.splitlines())
        total_complexity = 0
        
        try:
            tree = ast.parse(file_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Check for missing docstrings
                    if not ast.get_docstring(node):
                        issues.append(f"Missing docstring for function '{node.name}' on line {node.lineno}.")
                    
                    # Calculate cyclomatic complexity for the function
                    complexity = 1
                    for sub_node in ast.walk(node):
                        if isinstance(sub_node, (ast.If, ast.For, ast.While, ast.And, ast.Or, ast.With, ast.AsyncFor, ast.AsyncWith, ast.ExceptHandler)):
                            complexity += 1
                    if complexity > 5:
                         issues.append(f"High cyclomatic complexity ({complexity}) in function '{node.name}'. Consider refactoring.")
                    total_complexity += complexity

        except SyntaxError as e:
            return {"status": "error", "message": f"Syntax error in file: {e}"}

        return {
            "status": "analysis_complete",
            "line_count": line_count,
            "overall_complexity_score": total_complexity,
            "issues_found": issues if issues else "No major issues found.",
            "suggestions": ["Ensure all functions and classes have docstrings.", "Aim for a cyclomatic complexity below 5 for each function."]
        }

    def generate_documentation(self, file_content: str, file_path: str) -> dict:
        """
        Generates markdown documentation for a given code file by identifying classes and functions.
        Args:
            file_content: The string content of the code file.
            file_path: The path to the file, used for context in the documentation.
        Returns:
            A dictionary containing the generated documentation.
        """
        print(f"CodeAuditAgent: Generating documentation for {file_path}...")
        
        doc_body = ""
        try:
            tree = ast.parse(file_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    doc_body += f"\n### Class: `{node.name}`\n"
                    if ast.get_docstring(node):
                        doc_body += f"> {ast.get_docstring(node)}\n\n"
                elif isinstance(node, ast.FunctionDef):
                    args = [a.arg for a in node.args.args]
                    doc_body += f"- `def {node.name}({', '.join(args)}):`\n"
                    if ast.get_docstring(node):
                         doc_body += f"  - **Docstring:** {ast.get_docstring(node)}\n"
                    else:
                         doc_body += f"  - **Purpose:** [TODO: Describe the function's purpose.]\n"
        except SyntaxError as e:
            doc_body = f"Could not parse file due to syntax error: {e}"


        doc = f"""
# Documentation for `{file_path}`
This document provides an auto-generated overview of the code structure.

## Summary
The file contains {len(file_content.splitlines())} lines of code.

## Code Structure
{doc_body}
*Note: This is auto-generated documentation. Please review and fill in the details.*
"""
        return {"status": "documentation_generated", "documentation": doc}

    def scan_for_security_issues(self, dependency_file_content: str) -> dict:
        """
        Scans a dependency file's content to find known vulnerabilities.

        Args:
            dependency_file_content: The string content of the dependency file (e.g., requirements.txt).
        
        Returns:
            A dictionary containing the security scan results.
        """
        print(f"CodeAuditAgent: Scanning dependencies for security vulnerabilities...")
        vulnerabilities_found = []
        dependencies = dependency_file_content.splitlines()
        
        for dep in dependencies:
            dep = dep.strip()
            if not dep or dep.startswith('#'):
                continue
            
            # Regex to handle various version specifiers (==, >=, <=, ~, <, >)
            match = re.match(r'([a-zA-Z0-9_-]+)', dep)
            if not match:
                continue

            package_name = match.group(1).lower()

            if package_name in self.vulnerability_db:
                vulnerabilities_found.append({
                    "dependency": dep,
                    "issue_details": self.vulnerability_db[package_name]
                })

        return {
            "status": "scan_complete",
            "vulnerabilities": vulnerabilities_found if vulnerabilities_found else "No known vulnerabilities found in the provided dependencies.",
            "recommendation": "Always keep dependencies up to date and review their security advisories."
        }

    def create_qa_tests(self, file_content: str, function_name: str) -> dict:
        """
        Generates a unit test template for a specific function within a code file.

        Args:
            file_content: The string content of the code file.
            function_name: The name of the function to generate tests for.

        Returns:
            A dictionary containing the generated test suite.
        """
        print(f"CodeAuditAgent: Generating QA tests for function '{function_name}'...")
        
        try:
            tree = ast.parse(file_content)
            func_node = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == function_name:
                    func_node = node
                    break
            
            if not func_node:
                return {"status": "error", "message": f"Function '{function_name}' not found in the file."}

            args_with_types = []
            for arg in func_node.args.args:
                arg_str = arg.arg
                if arg.annotation:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                args_with_types.append(arg_str)


        except SyntaxError as e:
            return {"status": "error", "message": f"Syntax error in file: {e}"}

        test_code = f"""
import unittest
# TODO: Update this import to match the actual file name
# from your_module import {function_name} 

class Test{function_name.capitalize()}(unittest.TestCase):
    \"\"\"
    Test suite for the {function_name} function.
    \"\"\"

    def setUp(self):
        \"\"\"Set up test fixtures, if any.\"\"\"
        # self.fixture = ...
        pass

    def test_nominal_case(self):
        \"\"\"
        Tests the function with typical, expected inputs.
        Arguments: ({', '.join(args_with_types)})
        \"\"\"
        # Example:
        # result = {function_name}(...)
        # self.assertEqual(result, 'expected_output')
        self.assertTrue(True) # Placeholder assertion

    def test_edge_cases(self):
        \"\"\"
        Tests edge cases like empty inputs, None, or zero values.
        \"\"\"
        # Example for a function that takes a list:
        # self.assertEqual({function_name}([]), [])
        pass # TODO: Add edge case tests

    def test_invalid_input(self):
        \"\"\"
        Tests how the function handles invalid input types.
        \"\"\"
        # Example:
        # with self.assertRaises(TypeError):
        #     {function_name}(123) # Assuming it expects a string
        pass # TODO: Add invalid input tests

if __name__ == '__main__':
    unittest.main()
"""
        return {"status": "tests_generated", "test_suite_code": test_code, "function_tested": function_name}
