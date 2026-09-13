# AIKDAP - Engineering Constitution

Version: 1.0
Status: Frozen (MVP)

---

# Project

AIKDAP

AI Knowledge Discovery And Analytics Platform

AIKDAP is an AI Work Operating System designed to complete complex research and business analytics workflows using a multi-agent architecture.

The platform is NOT a chatbot.

The platform completes work.

---

# Product Vision

Users should never need to switch between multiple AI tools.

Instead of using:

- ChatGPT
- Perplexity
- NotebookLM
- Power BI
- Kaggle
- Google Search

AIKDAP provides one intelligent workspace.

The user gives ONE task.

The platform plans, distributes, executes, validates and delivers the complete result.

---

# Core Principle

AIKDAP completes work.

It does not simply answer questions.

Every feature should help users finish work.

---

# Architecture Status

Architecture Version

1.0

Status

FROZEN

Do NOT redesign the architecture.

Do NOT invent new features.

Do NOT rename modules.

Do NOT move components unless explicitly instructed.

---

# Enterprise Workflow

User

↓

Authentication

↓

Intent Analysis Agent

↓

Planner Agent

↓

LangGraph Orchestrator

↓

Specialized AI Agents

↓

Tool Registry

↓

Knowledge Layer

↓

Response Aggregator

↓

Validation Service

↓

Explainable AI

↓

Final Response

This workflow is frozen.

---

# Project Structure

Every user owns Projects.

Everything belongs to a Project.

Project

├── Overview
├── Research Workspace
├── Business Analytics
├── Knowledge Base
├── Assets
├── Reports
├── Workflow Timeline
└── Settings

---

# Workspace Memory

Short-term Memory

Redis

Long-term Memory

PostgreSQL

ChromaDB

Projects remember previous work.

Memory is project-based.

Not chat-based.

---

# Assets

Every generated artifact becomes a reusable asset.

Examples

Research Papers

Datasets

Reports

Dashboards

Executive Summaries

Roadmaps

Notes

Charts

Presentations

Assets should never disappear.

---

# Command Center

The application's homepage.

It should answer

"What is happening?"

Not

"What do you want to ask?"

---

# Tech Stack

Backend

Python 3.12+

FastAPI

SQLAlchemy 2

Alembic

PostgreSQL

Redis

Celery

Pydantic v2

Frontend

React

TypeScript

Vite

Tailwind CSS

shadcn/ui

TanStack Query

Backend Infrastructure

Docker

Docker Compose

Vector Database

ChromaDB

AI

LangGraph

LangChain

OpenAI-compatible LLMs

---

# Development Philosophy

Build incrementally.

Never generate the entire application.

Every milestone must be production-ready.

Every module must be independently testable.

---

# Current Milestones

Milestone 0

Backend Foundation

FastAPI

Configuration

Logging

Database

Docker

Health Endpoint

Milestone 1

Authentication

Users

JWT

Milestone 2

Projects

Project CRUD

Command Center Backend

Milestone 3

Workspace Memory

Assets

Knowledge Base

Milestone 4

LangGraph

Planner

Intent Analysis

Tool Registry

Milestone 5

Research Workspace

Milestone 6

Business Analytics

Milestone 7

Explainable AI

Milestone 8

Frontend

Milestone 9

Deployment

Milestone 10

Persona-Based Features (team-requested scope addition, approved 2026-09-13)

Persona (Student, Researcher, Project Builder; project override)

Paper Suggestions (OpenAlex) with Add & Re-run

Synopsis (Study Summary, Project Synopsis; DOCX/PDF)

Build Plan (tool and process recommendations)

Design: docs/superpowers/specs/2026-09-13-persona-features-design.md

---

# Folder Structure

Follow the existing repository.

Do NOT create additional root folders.

Do NOT restructure without approval.

---

# Coding Standards

Use Python type hints.

Use Pydantic v2.

Use SQLAlchemy 2.

Use dependency injection.

Use async endpoints where appropriate.

Avoid global variables.

Avoid circular imports.

Keep modules cohesive.

Functions should have a single responsibility.

Prefer composition over inheritance.

Never duplicate logic.

---

# FastAPI Standards

Feature-based architecture.

Every module should contain

router.py

service.py

schemas.py

models.py

repository.py

Dependencies only when required.

Business logic must never exist inside routers.

---

# API Standards

Version

/api/v1

JSON responses only.

Consistent response models.

Meaningful HTTP status codes.

Proper exception handling.

---

# Database Standards

Alembic only.

No manual schema changes.

UUID primary keys unless otherwise specified.

Use created_at

updated_at

timestamps.

Soft delete only when required.

---

# AI Standards

Planner decides execution.

LangGraph orchestrates.

Agents never call each other directly.

Communication occurs through shared state.

The Response Aggregator combines outputs.

Validation verifies completion.

---

# Explainable AI

Every workflow execution should be traceable.

Record

Planning

Agent execution

Tool usage

Validation

Aggregation

Execution time

---

# Security

JWT Authentication.

Secrets only in .env.

Never hardcode credentials.

Validate all inputs.

---

# Performance

Use async I/O.

Avoid blocking operations.

Cache appropriate responses with Redis.

Background tasks via Celery.

---

# Documentation

Every public module should contain documentation.

Every major architectural decision should be explained.

---

# Git

Small commits.

Meaningful commit messages.

One feature per commit.

---

# Code Generation Rules

When generating code:

Explain architecture first.

Generate only requested files.

Do not generate future milestones.

Do not invent APIs.

Do not redesign the project.

If assumptions are required,

state them explicitly.

---

# Definition of Done

A milestone is complete only if

Code compiles.

Application starts.

Tests pass.

Architecture is respected.

No placeholder logic.

No TODOs.

No duplicated code.

Production-quality implementation.

Only then proceed to the next milestone.

---

You are the Lead Backend Engineer for AIKDAP.

Implement carefully.

Optimize for maintainability, scalability, readability, and production quality.

Think like a Staff Engineer.

Never sacrifice architecture for speed.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
