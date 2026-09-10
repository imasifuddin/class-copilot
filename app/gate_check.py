"""Does it answer real doubts and stay quiet through ordinary classroom talk?

Runs the app's actual decision path against realistic utterances, using the
configured model. This is the question "will it interrupt general conversation"
turned into a number.
"""
import asyncio
import sys

from dotenv import load_dotenv

from . import config, settings_store
from .hub import Hub
from .trigger import QuestionDetector

ANSWER = [
    "what is the difference between a hash map and an array",
    "my loop keeps running forever and I cannot see why",
    "sir I did not understand recursion properly",
    "why do we need a virtual environment for every project",
    "my code prints none instead of the list",
    "can you show one example of a smart contract",
    "what happens if two blocks are mined at the same time",
    "I am getting index out of range error in my code",
    "explain the time complexity of binary search",
    "how is a linked list different from an array in memory",
    "the output is coming wrong when I use float instead of int",
    "sir ye inheritance aur composition me kya difference hai",
    "should we use a list or a set here",
    "what does the self keyword actually mean in python",
]

SKIP = [
    "can you hear me sir",
    "yes sir understood thank you",
    "good morning everyone how are you all",
    "sir when is the next class",
    "sir will you share the notes after the session",
    "my internet is very slow today",
    "okay so now we will open the editor and continue",
    "sir is my screen visible to everyone",
    "sorry sir I was on mute",
    "thank you so much sir see you tomorrow",
    "one minute sir my system is hanging",
    "did you all complete the assignment I gave last week",
    "please everyone join on time from tomorrow",
    "so that is all for today, any doubts we will take next class",
]


async def main():
    load_dotenv()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = config.load()
    settings_store.apply(cfg)
    cfg.trigger.smart = True
    print(f"model: {cfg.llm.provider} / {cfg.llm.model}\n")

    hub = Hub(cfg)
    hub.detector = QuestionDetector(cfg.trigger)

    async def verdict(text):
        return await hub._classify(text)

    tp = fn = tn = fp = 0
    print("SHOULD ANSWER (a real doubt)")
    for t in ANSWER:
        got = await verdict(t)
        ok = got is not None
        tp, fn = tp + ok, fn + (not ok)
        print(f"  {'ok  ' if ok else 'MISS'} {t[:62]}")

    print("\nSHOULD STAY QUIET (ordinary classroom talk)")
    for t in SKIP:
        got = await verdict(t)
        ok = got is None
        tn, fp = tn + ok, fp + (not ok)
        print(f"  {'ok  ' if ok else 'NOISE'} {t[:62]}")

    total = tp + fn + tn + fp
    print("\n" + "=" * 60)
    print(f"  doubts answered      {tp}/{len(ANSWER)}   (missed {fn})")
    print(f"  chatter ignored      {tn}/{len(SKIP)}   (interrupted {fp})")
    print(f"  overall              {(tp + tn)}/{total} = {(tp+tn)/total*100:.0f}%")

    # what the old keyword-only path would have done, for comparison
    kw_fp = sum(1 for t in SKIP if hub.detector.reason(t))
    print(f"\n  keyword-only would have interrupted {kw_fp}/{len(SKIP)} of the "
          f"ordinary conversation")
    for t in SKIP:
        r = hub.detector.reason(t)
        if r:
            print(f"      would have fired on: \"{t}\"  ({r})")

def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
