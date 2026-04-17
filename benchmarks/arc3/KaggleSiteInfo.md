
This competition requires identity verification
To submit to this competition, you'll need to verify your identity. Learn More


Verify now
Overview
Build systems that learn and adapt to novel, human-solvable tasks they’ve never seen before and advance AI’s ability to learn new skills efficiently.

Start

5 days ago
Close
7 months to go
Entry
Description
Note: there are three active competitions for ARC in 2026: this competition, ARC-AGI-2, and a paper track, where you can document your approach for either one of the prediction competitions.

Real intelligence isn’t about memorizing answers - it’s knowing what to do when the problem changes. Today’s AI systems excel at what they were trained to do, but often fall short when something unfamiliar comes along. Most benchmarks reward pattern recognition, not genuine problem-solving.

ARC Prize Foundation focuses on true generalization: whether a system can quickly learn new skills in unfamiliar situations. Instead of rewarding pattern recognition on known tasks, it evaluates how well systems can adapt to new problems they’ve never encountered before. The ARC-AGI-3 evaluation environment is designed so systems can't just memorize solutions. Tasks take place in hidden, interactive environments that require exploration and multi-step reasoning.

In this competition, you’ll build AI systems that adapt on the fly to new tasks in the ARC environment, and develop approaches that learn quickly, generalize well, and solve problems never seen before.

Your solution could help move AI closer to systems that learn the way people do: flexible, efficient, and able to handle new challenges.

The real test of intelligence begins when the problem changes.

Evaluation
Scores for individual game range from 0 to 100%. A score of 100% represents an agent matching human-level performance, meaning it beat every game while matching the number of actions humans took. While an agent could theoretically exceed 100% by using fewer moves, scores are capped at 100%. The final score averages individual game scores across levels. Read more at the ARC-AGI-3 Scoring Methodology page.

Submission File
Submission files are automatically calculated. As long as the agent takes action on any of the games, a submission file for all of the games is created.

Timeline
March 25, 2026 - Start Date.
October 26, 2026 - Entry Deadline. You must accept the competition rules before this date in order to compete.
October 26, 2026 - Team Merger Deadline. This is the last day participants may join or merge teams.
November 2, 2026 - Final Submission Deadline.
December 4, 2026 - Winners announcement.
All deadlines are at 11:59 PM UTC on the corresponding day unless otherwise noted. The competition organizers reserve the right to update the contest timeline if they deem it necessary.

Prizes
TOTAL PRIZES AVAILABLE: $850,000
Progress Prizes: $150,000
Bonus Prize: $700,000
In line with the spirit of the competition, participants eligible for a prize will be removed from the competition if they do not open source their solutions.

ARC-AGI-3 Progress Prizes: $150,000
Final Leaderboard Prizes: $75,000
These prizes are based on the leaderboard score at the end of the competition.

First Prize: $40,000
Second Prize: $15,000
Third Prize: $10,000
Fourth Prize: $5,000
Fifth Prize: $5,000
Milestone Prizes: $75,000
These prizes are based on the leaderboard score on two specific dates throughout the competition. Notebooks must be made public under an open source license by the corresponding milestone dates to qualify for these prizes.

Milestone 1: June 30th, 2026

First Prize: $25,000
Second Prize: $7,500
Third Prize: $5,000
Milestone 2: September 30, 2026

1st Prize: $25,000
2nd Prize: $7,500
3rd Prize: $5,000
Bonus Prize
A Grand Prize of an additional $700,000 will be unlocked in the event that a team achieves a score of 100% accuracy on the competition leaderboard. At the end of the competition, the Grand Prize will be divided among the Top 5 teams that have achieved 100% accuracy as outlined below.

First Prize: $350,000
Second Prize: $175,000
Third Prize: $70,000
Fourth Prize: $70,000
Fifth Prize: $35,000
Code Requirements


Submissions to this competition must be made through Notebooks. In order for the "Submit" button to be active after a commit, the following conditions must be met:

CPU Notebook <= 6 hours run-time
GPU Notebook <= 6 hours run-time
Internet access disabled
Freely & publicly available external data is allowed, including pre-trained models
Submission file will be automatically generated.
Please see the Code Competition FAQ for more information on how to submit. And review the code debugging doc if you are encountering submission errors.

Upgraded accelerators
Upgraded accelerators will be added to this competition in the near future, stay tuned for more details.

Citation
Francois Chollet, Mike Knoop, Greg Kamradt, David Wexler, Derek Smith, Hunter Henry, Walter Reade, and María Cruz. ARC Prize 2026 - ARC-AGI-3. https://kaggle.com/competitions/arc-prize-2026-arc-agi-3, 2026. Kaggle.

Dataset Description
ARC-AGI-3 is an Interactive Reasoning Benchmark designed to measure an AI agent's ability to generalize in novel, unseen environments. Unlike traditional static benchmarks used to evaluate LLMs and reasoning systems, ARC-AGI-3 evaluates frontier AI agent systems on exploration, memory, goal acquisition, and alignment.

Full documentation: docs.arcprize.org

Games (Environments)
ARC-AGI-3 consists of hand-crafted interactive environments that test abstraction and reasoning. Each game presents a unique challenge that your agent must explore, understand, and solve.

How Games Work
Your agent receives frames — JSON objects containing the current game state and metadata.
Each frame includes a grid (max 64×64) with integer cell values 0–15 representing different states/colors, using a (0,0) top-left coordinate system.
Your agent responds with actions to interact with the environment.
Each game has multiple levels of increasing difficulty.
A game can be in one of three states: NOT_FINISHED, WIN, or GAME_OVER.
Available Actions
Agents interact with environments using up to 7 actions:

Action	Description
RESET	Start or restart the game
ACTION1 – ACTION5	Simple actions (e.g., move up/down/left/right, interact)
ACTION6	Complex action requiring (x, y) coordinates
ACTION7	Additional simple action
Each game defines which actions are available and what they do. The meaning of actions varies per game — your agent must figure out what each action does through exploration.

Public Games
A set of public games is available for development and practice at arcprize.org. In addition, public game files are available in the environment_files folder on this page.

Note: Competition evaluation uses a separate, private set of 110 games that your agent has never seen. Half of these are used for the Public Leaderboard score, and the other half for the Private Leaderboard score.

Scoring
AI agents are scored on two criteria:

Completion — How many levels did the agent complete in each game?
Efficiency — How many actions did the agent take to complete each level, compared to a human baseline?
Scoring Method
For each level completed, the agent's action count is compared to a human baseline (first-time test-testers).
Per-level score = min(human_actions / agent_actions, 1.0), then squared (a raw score of 0.5 becomes 0.25).
Per-game score = Weighted average of level scores (weighted by level index, 1-indexed).
Total score = Average of all individual game scores.
Final output is a score between 0%–100%.
ARC-AGI Toolkit
The arc-agi Python package provides the core toolkit for interacting with ARC-AGI-3 environments.

Building Agents
The ARC-AGI-3-Agents repository provides the framework for building and running agents.

Agent Architecture
An agent plays ARC-AGI-3 by implementing two core methods:

is_done(frames, latest_frame) — Decide if the agent should stop playing
choose_action(frames, latest_frame) — Choose which action to take given the current game state
A Swarm orchestrates multiple agent instances across all available games in parallel.

Agent Lifecycle
Get the list of available games from the API
Open a scorecard (tracks performance)
For each game, RESET to start, then take actions based on the agent's strategy
Close the scorecard when all games are complete
Files
ARC-AGI-3-Agents/ - a local copy of the ARC-AGI-3-Agents repo.
arc_agi_3_wheels/ - package wheels for the installing ARC-AGI-3.
environment_files/ - location of game files; during the notebook rerun, a new set of games will be swapped in to this folder.
Files
148 files

Size
48.2 MB

Type
py, whl, json + 14 others

License
Apache 2.0

ARC-AGI-3-Agents(3 directories, 11 files)
.git

5 directories, 5 files
agents

1 directories, 6 files
tests

1 directories, 3 files
.env.example

599 B
.gitignore

297 B
.pre-commit-config.yaml

394 B
.python-version

4 B
LICENSE

1.07 kB
README.md

4.56 kB
llms.txt

2.84 kB
main.py

6.2 kB
pyproject.toml

818 B
pytest.ini

405 B
uv.lock

408.17 kB
Data Explorer
48.2 MB

ARC-AGI-3-Agents

.git

agents

tests

.env.example

.gitignore

.pre-commit-config.yaml

.python-version

LICENSE

README.md

llms.txt

main.py

pyproject.toml

pytest.ini

uv.lock

arc_agi_3_wheels

environment_files

Summary
148 files


Download All
kaggle competitions download -c arc-prize-2026-arc-agi-3
Download using Kaggle CLI

kagglehub.competition_download('arc-prize-2026-arc-agi-3')
kagglehub

Prompt your client to use the "mcp_kaggle_download_competition_data_files" tool.
MCP

Metadata
License
Apache 2.0