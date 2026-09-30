#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

task_info = {
    "name": "batchdag",
    "title": "Test Batch Task with subtask dependencies",
    "official_language": "",
    "submission_format_choice": "other",
    "submission_format": "batchdag.%l",
    "time_limit_{{dataset_id}}": "0.5",
    "memory_limit_{{dataset_id}}": "128",
    "task_type_{{dataset_id}}": "Batch",
    "TaskTypeOptions_{{dataset_id}}_Batch_compilation": "alone",
    "TaskTypeOptions_{{dataset_id}}_Batch_io_0_inputfile": "",
    "TaskTypeOptions_{{dataset_id}}_Batch_io_1_outputfile": "",
    "TaskTypeOptions_{{dataset_id}}_Batch_output_eval": "diff",
    "score_type_{{dataset_id}}": "GroupMin",
    # Subtask 0 = testcase 000 (input 2), subtask 1 = 001 (input 1,
    # depends on 0), subtask 2 = 002 (input 3). half-correct fails only
    # even inputs: 0 in subtask 0, so subtask 1 is worth 0 by the
    # dependency (80 without it), 50 from subtask 2.
    "score_type_parameters_{{dataset_id}}":
        '[{"max_score": 20, "testcases": 1},'
        ' {"max_score": 30, "testcases": 1, "depends_on": [0]},'
        ' {"max_score": 50, "testcases": 1}]',
}

test_cases = [
    ("1.in", "1.out", True),
    ("2.in", "2.out", True),
    ("3.in", "3.out", True),
]
