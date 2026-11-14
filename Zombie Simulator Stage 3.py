"""
CPS310 - 2026 Fall - Assignment #3
Zombie Simulator - Stage 3

Group Members:
1. Sean Sanpietro
2. Ho'oa Bode

"""

import argparse
import math
import os
import random
import sqlite3
import time
from statistics import median
from concurrent.futures import ProcessPoolExecutor

WIDTH = 25
HEIGHT = 10
MAX_HUMANS = WIDTH * HEIGHT

STARTING_HUMANS = 200
STARTING_ZOMBIES = 50
MAX_ROUNDS = 100

HUMAN_REPRODUCTION_CHANCE = 0.01
ZOMBIE_DEATH_CHANCE = 0.01

WORKERS = min(4, max(2, os.cpu_count() or 2))
ROUND_DELAY = 0.5
RESET_DATABASE = True

MINI_MAP_WIDTH = 25
MINI_MAP_HEIGHT = 10


class GameState:
    def __init__(self, round_number, humans, zombies):
        self.round_number = round_number
        self.humans = humans
        self.zombies = zombies


class MemoryStorage:
    def __init__(self):
        self.state = None

    def load(self):
        return self.state

    def save(self, state):
        self.state = state


class DatabaseStorage:
    def __init__(self, file_name):
        self.connection = sqlite3.connect(file_name)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime (round_number INTEGER)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS humans (x INTEGER, y INTEGER)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS zombies (x INTEGER, y INTEGER)"
        )
        self.connection.commit()

    def clear(self):
        self.connection.execute("DELETE FROM runtime")
        self.connection.execute("DELETE FROM humans")
        self.connection.execute("DELETE FROM zombies")
        self.connection.commit()

    def load(self):
        round_data = self.connection.execute(
            "SELECT round_number FROM runtime"
        ).fetchone()

        if round_data is None:
            return None

        humans = self.connection.execute("SELECT x, y FROM humans").fetchall()
        zombies = self.connection.execute("SELECT x, y FROM zombies").fetchall()
        return GameState(round_data[0], humans, zombies)

    def save(self, state):
        self.connection.execute("DELETE FROM runtime")
        self.connection.execute("DELETE FROM humans")
        self.connection.execute("DELETE FROM zombies")

        self.connection.execute(
            "INSERT INTO runtime VALUES (?)", (state.round_number,)
        )
        self.connection.executemany("INSERT INTO humans VALUES (?, ?)", state.humans)
        self.connection.executemany("INSERT INTO zombies VALUES (?, ?)", state.zombies)
        self.connection.commit()

    def close(self):
        self.connection.close()


# These functions are outside the class so Windows can run them in processes.
def split_agents(agent_list):
    chunks = []
    chunk_size = max(1, math.ceil(len(agent_list) / WORKERS))

    for index in range(0, len(agent_list), chunk_size):
        chunks.append(agent_list[index:index + chunk_size])

    return chunks


def move_humans_worker(data):
    humans, seed = data
    generator = random.Random(seed)
    moved_humans = []

    for human in humans:
        x = human[0]
        y = human[1]
        direction = generator.randint(1, 4)

        if direction == 1:
            x += 1
        elif direction == 2:
            x -= 1
        elif direction == 3:
            y += 1
        else:
            y -= 1

        x = max(0, min(WIDTH - 1, x))
        y = max(0, min(HEIGHT - 1, y))
        moved_humans.append((x, y))

    return moved_humans


def find_neighbors_worker(data):
    zombies, humans = data
    moved_zombies = []

    for zombie in zombies:
        closest_human = humans[0]
        closest_distance = math.dist(zombie, closest_human)

        for human in humans:
            distance = math.dist(zombie, human)

            if distance < closest_distance:
                closest_distance = distance
                closest_human = human

        x = zombie[0]
        y = zombie[1]

        if x < closest_human[0]:
            x += 1
        elif x > closest_human[0]:
            x -= 1

        if y < closest_human[1]:
            y += 1
        elif y > closest_human[1]:
            y -= 1

        moved_zombies.append((x, y))

    return moved_zombies


def process_events_worker(data):
    humans, zombie_locations, human_locations, seed, width, height = data
    generator = random.Random(seed)
    survivors = []
    infected = []
    babies = []

    for human in humans:
        x = human[0]
        y = human[1]

        if (x, y) in zombie_locations:
            infected.append((x, y))
            continue

        survivors.append((x, y))
        nearby_human = False

        for x_change in [-1, 0, 1]:
            for y_change in [-1, 0, 1]:
                if x_change == 0 and y_change == 0:
                    continue

                if (x + x_change, y + y_change) in human_locations:
                    nearby_human = True

        if nearby_human and generator.random() < HUMAN_REPRODUCTION_CHANCE:
            baby_x = x + generator.choice([-1, 0, 1])
            baby_y = y + generator.choice([-1, 0, 1])
            baby_x = max(0, min(width - 1, baby_x))
            baby_y = max(0, min(height - 1, baby_y))
            babies.append((baby_x, baby_y))

    return survivors, infected, babies


def zombie_death_worker(data):
    zombies, seed = data
    generator = random.Random(seed)
    remaining_zombies = []

    for zombie in zombies:
        if generator.random() > ZOMBIE_DEATH_CHANCE:
            remaining_zombies.append(zombie)

    return remaining_zombies


def process_events(humans, zombies, width, height, generator, executor=None):
    """Apply one event phase to the moved populations and return both lists."""
    zombie_locations = set(zombies)
    human_locations = set(humans)
    jobs = []
    for chunk in split_agents(humans):
        seed = generator.randrange(2**64)
        jobs.append((chunk, zombie_locations, human_locations, seed, width, height))
    dispatch = map if executor is None else executor.map

    survivors = []
    infected = []
    proposed_births = []
    for living, new_zombies, babies in dispatch(process_events_worker, jobs):
        survivors.extend(living)
        infected.extend(new_zombies)
        proposed_births.extend(babies)

    occupied = set(survivors) | zombie_locations
    for baby in proposed_births:
        if len(survivors) >= width * height:
            break
        if baby not in occupied:
            survivors.append(baby)
            occupied.add(baby)

    death_jobs = [
        (chunk, generator.randrange(2**64))
        for chunk in split_agents(zombies + infected)
    ]
    remaining_zombies = []
    for group in dispatch(zombie_death_worker, death_jobs):
        remaining_zombies.extend(group)
    return survivors, remaining_zombies


def check_events(executor):
    # Two humans share an infected cell. Both must convert independently.
    humans = [(0, 0), (0, 0), (3, 3)]
    zombies = [(0, 0)]
    expected = ([(3, 3)], [(0, 0)] * 3)
    for pool in (None, executor):
        assert process_events(humans, zombies, 4, 4, random.Random(1), pool) == expected
        assert process_events([], [], 4, 4, random.Random(1), pool) == ([], [])

    # Seeds 206 and 849 make both humans propose a birth at (0, 1).
    class BirthSeeds:
        def __init__(self):
            self.seeds = iter((206, 849))

        def randrange(self, stop):
            return next(self.seeds)

    for pool in (None, executor):
        result = process_events([(0, 0), (1, 0)], [], 3, 3, BirthSeeds(), pool)
        assert result == ([(0, 0), (1, 0), (0, 1)], [])


def benchmark_events():
    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        check_events(executor)
        print("Event checks passed. Workers:", WORKERS)
        print("Median of 5 trials per mode (seconds). Speedup = serial / parallel.")
        print("Grid       Humans  Zombies     Serial   Parallel  Speedup")
        for side, human_count, zombie_count in (
            (25, 200, 50),
            (150, 10_000, 2_500),
            (400, 50_000, 12_500),
            (800, 200_000, 50_000),
        ):
            setup = random.Random(310)
            humans = [
                (cell % side, cell // side)
                for cell in setup.sample(range(side * side), human_count)
            ]
            zombies = [(setup.randrange(side), setup.randrange(side))
                       for _ in range(zombie_count)]
            timings = {None: [], executor: []}
            for trial in range(5):
                results = {}
                modes = (None, executor) if trial % 2 == 0 else (executor, None)
                for pool in modes:
                    generator = random.Random(trial)
                    start = time.perf_counter()
                    results[pool] = process_events(
                        humans, zombies, side, side, generator, pool
                    )
                    timings[pool].append(time.perf_counter() - start)
                assert results[None] == results[executor], f"State mismatch at {side=}, {trial=}"
            serial = median(timings[None])
            parallel = median(timings[executor])
            print(f"{side:>3}x{side:<3} {human_count:>8,} {zombie_count:>8,}"
                  f" {serial:>10.6f} {parallel:>10.6f} {serial / parallel:>8.2f}x")


class ZombieSimulation:
    def __init__(self, storage):
        self.storage = storage

    def create_state(self):
        cells = []

        for x in range(WIDTH):
            for y in range(HEIGHT):
                cells.append((x, y))

        random.shuffle(cells)
        humans = cells[:STARTING_HUMANS]
        zombies = []

        for i in range(STARTING_ZOMBIES):
            x = random.randint(0, WIDTH - 1)
            y = random.randint(0, HEIGHT - 1)
            zombies.append((x, y))

        return GameState(0, humans, zombies)

    def run_parallel(self, executor, worker, jobs):
        all_agents = []
        results = executor.map(worker, jobs)

        for group in results:
            all_agents.extend(group)

        return all_agents

    def run(self):
        state = self.storage.load()

        if state is None:
            state = self.create_state()

        if not state.humans or not state.zombies:
            print("The saved game has already ended.")
            return

        with ProcessPoolExecutor(max_workers=WORKERS) as executor:
            while state.round_number < MAX_ROUNDS:
                state.round_number += 1
                round_start = time.perf_counter()

                human_jobs = []
                for chunk in split_agents(state.humans):
                    human_jobs.append((chunk, random.randint(1, 1000000)))
                state.humans = self.run_parallel(
                    executor, move_humans_worker, human_jobs
                )

                neighbor_start = time.perf_counter()
                neighbor_jobs = []
                for chunk in split_agents(state.zombies):
                    neighbor_jobs.append((chunk, state.humans))
                state.zombies = self.run_parallel(
                    executor, find_neighbors_worker, neighbor_jobs
                )
                neighbor_time = time.perf_counter() - neighbor_start

                event_start = time.perf_counter()
                state.humans, state.zombies = process_events(
                    state.humans, state.zombies, WIDTH, HEIGHT, random, executor
                )
                event_time = time.perf_counter() - event_start

                database_start = time.perf_counter()
                self.storage.save(state)
                database_time = time.perf_counter() - database_start
                round_time = time.perf_counter() - round_start

                self.display_map(state)
                print(
                    "Round time: %.3fs | Neighbor finding: %.3fs | Events: %.3fs | Database: %.3fs"
                    % (round_time, neighbor_time, event_time, database_time)
                )

                if len(state.humans) == 0:
                    print("All humans have been infected!")
                    print("The zombies win!")
                    return

                if len(state.zombies) == 0:
                    print("There are no zombies left!")
                    print("The humans survive!")
                    return

                time.sleep(ROUND_DELAY)

    def display_map(self, state):
        grid = []

        for y in range(MINI_MAP_HEIGHT):
            row = []
            for x in range(MINI_MAP_WIDTH):
                row.append(".")
            grid.append(row)

        for x, y in state.humans:
            map_x = x * MINI_MAP_WIDTH // WIDTH
            map_y = y * MINI_MAP_HEIGHT // HEIGHT
            grid[map_y][map_x] = "H"

        for x, y in state.zombies:
            map_x = x * MINI_MAP_WIDTH // WIDTH
            map_y = y * MINI_MAP_HEIGHT // HEIGHT
            grid[map_y][map_x] = "Z"

        print("\nROUND", state.round_number)
        for row in grid:
            print("".join(row))
        print("Humans:", len(state.humans), "/", MAX_HUMANS)
        print("Zombies:", len(state.zombies))


def main():
    print("Starting agents:", STARTING_HUMANS + STARTING_ZOMBIES)
    print("Workers:", WORKERS)

    storage = DatabaseStorage("zombie_stage3.db")

    if RESET_DATABASE:
        storage.clear()

    # storage = MemoryStorage()

    game = ZombieSimulation(storage)
    game.run()

    if isinstance(storage, DatabaseStorage):
        storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPS310 Zombie Simulator Stage 3")
    parser.add_argument("--benchmark", action="store_true", help="check and time the event phase")
    args = parser.parse_args()
    if args.benchmark:
        benchmark_events()
    else:
        main()
