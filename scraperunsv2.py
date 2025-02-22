import json
import logging
import os
import pickle
import random
import sys
from threading import Thread

import speedruncompy as speedruncompy
from speedruncompy.api import *
from speedruncompy.endpoints import *
from speedruncompy.enums import *

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_log = logging.getLogger("SpeedStats-V2")
_log.setLevel(logging.DEBUG)

fh = logging.FileHandler("logs/output.log", mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)

ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

_log.addHandler(fh)
_log.addHandler(ch)

CONCURRENT_THREADS = 2
GAME_BATCH_SIZE = 90

runs = []
l_temp_run = []
series = {}
games = {}
categories = {}
subcategories = {}
subcategoryValues = {}
levels = {}
groups = {}

platforms = {}
players = {}

excludedGames = [
    "w6jrzxdj",
    "o1y7pv1q",
]  # Speed Builders (API can't handle), White Tile 4 (Crashes website)
excludedCategories = ["n2y350ed", "5dw43j0k"]  # Subway Surfers - No Coins (API can't handle)


class Run:
    def __init__(self, seriesId: str, timeDirection: int, defaultTimer: int, run: dict):
        isLevelRun = run.get("levelId") is not None
        levelId = run.get("levelId") if isLevelRun else ""
        groupHash = run.get("categoryId") + levelId + "".join(run.get("valueIds"))

        if groupHash not in groups:
            subcategoryValueNames = []
            for valueId in run.get("valueIds"):
                subcategoryValueName = subcategoryValues.get(valueId)
                if subcategoryValueName is not None:
                    subcategoryValueNames.append(subcategoryValueName)

            levelText = ", " + levels.get(run.get("levelId")) if isLevelRun else ""
            subcategoryText = (
                " - " + ", ".join(subcategoryValueNames) if subcategoryValueNames else ""
            )
            groupName = (
                games.get(run.get("gameId"))
                + ": "
                + categories.get(run.get("categoryId"))
                + levelText
                + subcategoryText
            )
        else:
            groupName = groups.get(groupHash)

        self.groupName = groupName
        self.seriesName = series.get(seriesId)
        self.gameId = run.get("gameId")
        self.gameName = games.get(run.get("gameId"))
        self.time = self.getTime(run, defaultTimer)
        self.date = run.get("date")  # can be 0
        self.dateSubmitted = (
            run.get("dateSubmitted") if run.get("dateSubmitted") is not None else 2147483647
        )
        self.isLevelRun = isLevelRun
        self.isReverseTime = timeDirection == 1
        self.defaultTimer = defaultTimer
        self.platformName = platforms.get(run.get("platformId"))  # can be None
        self.playerNames = [players.get(playerId) for playerId in run.get("playerIds")]

    def getTime(self, run: dict, defaultTimer: int):
        if defaultTimer in {0, 1}:  # If default timing is RTA or LRT, check 'time' before 'igt'
            if run.get("time") is not None:
                return run.get("time")
            elif run.get("timeWithLoads") is not None:
                return run.get("timeWithLoads")
            elif run.get("igt") is not None:
                return (
                    run.get("igt") + 10000000.0
                )  # Makes IGT slower, but handles categories w/o RTA correctly
        elif run.get("igt") is not None:
            return run.get("igt")
        elif run.get("time") is not None:  # Either RTA or LRT depending on game
            return run.get("time")
        elif run.get("timeWithLoads") is not None:  # RTA for games that use LRT
            return run.get("timeWithLoads")
        _log.warning(f"Run with id {run.get('id')} has a null time.")
        return None

    def toDict(self):
        return {
            "groupName": self.groupName,
            "seriesName": self.seriesName,
            "gameName": self.gameName,
            "gameId": self.gameId,
            "time": self.time,
            "date": self.date,
            "dateSubmitted": self.dateSubmitted,
            "isLevelRun": self.isLevelRun,
            "isReverseTime": self.isReverseTime,
            "deafultTimer": self.defaultTimer,
            "platformName": self.platformName,
            "playerNames": self.playerNames,
        }


def testEndpoint(request: BaseRequest):
    # _log.info(type(request).__name__)
    try:
        response = request.perform()
        _log.info(json.dumps(response))
        return response
    except APIException as e:
        _log.error("API Error!", exc_info=e)
        return e


def joinThreads(threads: list, extend: bool = True):
    returnValues = []
    failed = 0
    for idx, t in enumerate(threads):
        if (returnValue := t.join()) is not None:  # Always None for normal Threads
            returnValues.extend(returnValue) if extend else returnValues.append(returnValue)
        elif type(t) == ReturnThread:
            if failed < 2:
                failed += 1
                _log.warning(f"Thread {idx} failed to return a value. Retrying...")
            else:
                raise ValueError("A Return Thread returned None.")
    threads.clear()
    return returnValues


def getOverviews(elements: list):
    overviews = []
    for element in elements:
        overview = {"id": element["id"], "name": element["name"]}
        overviews.append(overview)
    return overviews


def explorePages(requestType: str, request: type, listKey: str, groupsOf: int = CONCURRENT_THREADS):
    pageThreads = []
    overviews = []
    _log.info(f"Requesting {requestType} on page 1")
    firstPage = request(page=1).perform()
    overviews.extend(getOverviews(firstPage[listKey]))
    totalPages = firstPage["pagination"]["pages"]

    for page in range(2, totalPages + 1):
        t = ReturnThread(target=request(page=page).perform)
        pageThreads.append(t)
        _log.info(f"Requesting {requestType} on page {page}")
        t.start()

        if page % groupsOf == 0:
            for pageData in joinThreads(pageThreads, extend=False):
                overviews.extend(getOverviews(pageData[listKey]))

    for pageData in joinThreads(pageThreads, extend=False):
        overviews.extend(getOverviews(pageData[listKey]))
    return overviews


def exploreList(list: list, globalMap: dict, target: object, groupsOf: int = CONCURRENT_THREADS):
    elementThreads = []
    subElements = []
    elementsExplored = 0
    for element in list:
        id = element["id"]
        if id not in globalMap:
            elementsExplored += 1
            globalMap[id] = element["name"].strip()
            t = ReturnThread(target=target, args=(element,))
            elementThreads.append(t)
            t.start()

            if elementsExplored % groupsOf == 0:
                subElements.extend(joinThreads(elementThreads))

    subElements.extend(joinThreads(elementThreads))
    return subElements


def exploreLeaderboardRequests(list: list, groupsOf: int = CONCURRENT_THREADS):
    requestThreads = []
    for requestsExplored, request in enumerate(list, start=1):
        t = Thread(
            target=exploreLeaderboard, args=(request["category"], request["page"], request["type"])
        )
        requestThreads.append(t)
        t.start()

        if requestsExplored % groupsOf == 0:
            joinThreads(requestThreads)

    joinThreads(requestThreads)


def exploreLeaderboard(categoryOverview: dict, page: int = 1, type: int = 1):
    seriesId = categoryOverview["seriesId"]
    gameId = categoryOverview["gameId"]
    categoryId = categoryOverview["id"]
    timeDirection = categoryOverview["timeDirection"]
    defaultTimer = categoryOverview["defaultTimer"]

    runBatch = {}
    _log.info(
        f"Getting run batch for game {games[gameId]} and category"
        f" {categories[categoryId]} on page {page} with leaderboard type {type}"
    )

    if type == 1:
        runBatch = GetGameLeaderboard(
            gameId, categoryId, obsolete=1, video=0, verified=1, page=page
        ).perform()["leaderboard"]
        for player in runBatch["players"]:
            if len(player["id"]) != 38:  # Not a guest user
                playerName = player["name"].strip()
            else:
                playerName = f"[Guest]{player['name'].strip()}"
            players[player["id"]] = playerName

        for run in runBatch["runs"]:
            l_temp_run.append(Run(seriesId, timeDirection, defaultTimer, run))
        # runs.extend(l_temp_run)
        runs = l_temp_run
    else:
        runBatch = GetGameLeaderboard2(
            gameId, categoryId, obsolete=1, video=0, verified=1, page=page
        ).perform()
        for player in runBatch["playerList"]:
            if len(player["id"]) != 38:  # Not a guest user
                playerName = player["name"].strip()
            else:
                playerName = f"[Guest]{player['name'].strip()}"
            players[player["id"]] = playerName
        for run in runBatch["runList"]:
            l_temp_run.append(Run(seriesId, timeDirection, defaultTimer, run))
        # runs.extend(l_temp_run)
        runs = l_temp_run

    return runBatch["pagination"]["pages"]


def exploreCategory(categoryOverview: dict):
    if categoryOverview["id"] in excludedCategories:
        return None

    type = random.choice([1, 2])
    totalPages = exploreLeaderboard(categoryOverview, page=1, type=type)
    leaderboardRequests = []
    for page in range(2, totalPages + 1):
        leaderboardRequest = {"category": categoryOverview, "page": page, "type": type}
        leaderboardRequests.append(leaderboardRequest)

    return leaderboardRequests


def exploreGame(gameOverview: dict):
    if gameOverview["id"] in excludedGames:
        return None

    seriesId = gameOverview.get("seriesId")
    gameId = gameOverview["id"]

    _log.info(f"Requesting data for game {gameOverview['name']}")
    game = GetGameData(gameId).perform()

    if game is None:
        return None

    defaultTimer = game["game"]["defaultTimer"]

    gameLevels = game["levels"]
    for level in gameLevels:
        levels[level["id"]] = level["name"].strip()

    gamePlatforms = game["platforms"]
    for platform in gamePlatforms:
        platforms[platform["id"]] = platform["name"].strip()

    gameVariables = game["variables"]
    for variable in gameVariables:
        if variable["isSubcategory"] is True:
            subcategories[variable["id"]] = variable["name"].strip()

    gameValues = game["values"]
    for value in gameValues:
        if subcategories.get(value["variableId"]) is not None:
            subcategoryValues[value["id"]] = value["name"].strip()

    categoryOverviews = []
    for category in game["categories"]:
        categoryOverview = {
            "seriesId": seriesId,
            "gameId": gameId,
            "id": category["id"],
            "name": category["name"],
            "timeDirection": category["timeDirection"],
            "defaultTimer": defaultTimer,
        }
        categoryOverviews.append(categoryOverview)

    return categoryOverviews


def exploreSeries(seriesOverview: dict):
    seriesId = seriesOverview["id"]
    _log.info(f"Requesting games for series {series[seriesId]}")

    if seriesId == "15ndxp7r":  # Harry Potter
        seriesGameList = GetSeriesGames(seriesId=seriesId, max=200).perform()["data"]
    else:
        seriesGameList = GetGameList(seriesId=seriesId).perform()["gameList"]

    seriesGameOverviews = []
    for game in seriesGameList:
        gameOverview = {
            "seriesId": seriesId,
            "id": game["id"],
            "name": game["names"]["international"] if seriesId == "15ndxp7r" else game["name"],
        }
        seriesGameOverviews.append(gameOverview)

    return seriesGameOverviews


def dumpData(path: str, runs_to_dump: list = runs):
    runsDict = [run.toDict() for run in runs_to_dump]
    runsJson = json.dumps(runsDict)
    with open(path, "w") as file:
        file.write(runsJson)


def testSeries(path: str, seriesId: str, seriesName: str):
    series[seriesId] = seriesName
    gameQueue = exploreSeries({"id": seriesId})
    categoryQueue = exploreList(gameQueue, games, exploreGame)
    leaderboardRequestQueue = exploreList(categoryQueue, categories, exploreCategory)
    exploreLeaderboardRequests(leaderboardRequestQueue)
    dumpData(path)


def testGame(path: str, gameId: str, gameName: str):
    gameQueue = [{"seriesId": None, "id": gameId, "name": gameName}]
    categoryQueue = exploreList(gameQueue, games, exploreGame)
    leaderboardRequestQueue = exploreList(categoryQueue, categories, exploreCategory)
    exploreLeaderboardRequests(leaderboardRequestQueue)
    dumpData(path)


def exploreAll(path: str, force_refresh: bool = False):
    _log.info(f"Will output runs to path {path}")
    seriesQueue = explorePages("series", GetSeriesList, "seriesList")

    # Check if gameQueue has already been pickled
    if os.path.isfile("data/gameQueue.pkl") and not force_refresh:
        with open("data/gameQueue.pkl", "rb") as file:
            gameQueue = pickle.load(file)
    else:
        gameQueue = exploreList(seriesQueue, series, exploreSeries)  # Queues all series games
        gameQueue.extend(
            explorePages("games", GetGameList, "gameList")
        )  # Queues all normal games, duplicates will be skipped later

        # Drop gameQueue as a pickle
        with open("data/gameQueue.pkl", "wb") as file:
            pickle.dump(gameQueue, file)

    # Extract batches of games
    gameBatches = [
        gameQueue[x : x + GAME_BATCH_SIZE] for x in range(0, len(gameQueue), GAME_BATCH_SIZE)
    ]
    for idx, gameBatch in enumerate(gameBatches):
        # Check if batch has not already been processed
        if os.path.isfile(f"{path[:-5]}_{idx}.json") and not force_refresh:
            print(f"Skipping batch {idx} as it has already been processed.")
            continue
        # Clear runs from previous batch
        l_temp_run.clear()
        # Explore the batch
        categoryQueue = exploreList(gameBatch, games, exploreGame)
        leaderboardRequestQueue = exploreList(
            categoryQueue, categories, exploreCategory
        )  # Adds runs on page 1
        exploreLeaderboardRequests(leaderboardRequestQueue)  # Adds runs on pages 2 and beyond
        dumpData(
            f"{path[:-5]}_{idx}.json",
            l_temp_run,
        )
    # dumpData(path)
