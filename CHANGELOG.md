# Changelog

## [0.2.1](https://github.com/damien-robotsix/robotsix-memory/compare/v0.2.0...v0.2.1) (2026-09-05)


### Bug Fixes

* FastAPI app version string stale (0.1.0 vs package 0.2.0) (20260904T120021Z-fastapi-app-version-string-stale-0-1-0-v-2127) ([#9](https://github.com/damien-robotsix/robotsix-memory/issues/9)) ([63db9e0](https://github.com/damien-robotsix/robotsix-memory/commit/63db9e0d0bc04076367ec25fd3a18602ce7469fc))

## [0.2.0](https://github.com/damien-robotsix/robotsix-memory/compare/v0.1.0...v0.2.0) (2026-09-04)


### Features

* background retain on /remember (engine async mode) for fire-and-forget writes ([ef397bf](https://github.com/damien-robotsix/robotsix-memory/commit/ef397bf3dc8f485409b604a05b555aa550cab9d7))
* fleet memory component wrapping a Hindsight engine ([6dd0b0d](https://github.com/damien-robotsix/robotsix-memory/commit/6dd0b0d43a9844fbec7cae4cedd206b3afd18274))
* update_mode passthrough on /remember for rolling-summary dedup ([aa457f2](https://github.com/damien-robotsix/robotsix-memory/commit/aa457f2fe7ec641d23b5f21e72d5d338a7609321))


### Bug Fixes

* add central-deploy contract version header ([bb0a50f](https://github.com/damien-robotsix/robotsix-memory/commit/bb0a50f05a75a3697179ddcedd60a99d29b82d8d))
* align client with live Hindsight 0.9.2 API (retain POST /memories, recall POST body) ([326f8a2](https://github.com/damien-robotsix/robotsix-memory/commit/326f8a26a03fdaae42e3bc0ec58148b2e8d1ec61))
* deptry config — uvicorn is the CMD server, robotsix_memory is first-party ([b16755a](https://github.com/damien-robotsix/robotsix-memory/commit/b16755a1e75ed2f531abeabd2add0601a059e1e7))
* hindsight image tags carry no v prefix (0.9.2) ([7b12302](https://github.com/damien-robotsix/robotsix-memory/commit/7b123024f54683c6db538abe1333db9ea0e9904a))
* mark primary service for onboarding; drop redundant coverage-threshold input ([419964b](https://github.com/damien-robotsix/robotsix-memory/commit/419964b81c20c0d59733a512ed93cc520b072f61))
* move deployment contract to deploy/docker-compose.yml per onboarding contract ([651f200](https://github.com/damien-robotsix/robotsix-memory/commit/651f2006977ef217bd4e62ed90c688ae44d97da2))

## Changelog
