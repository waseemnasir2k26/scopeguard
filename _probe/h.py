import sys
sys.path.insert(0, ".")
from scopeguard.pipeline import analyze, Options
from scopeguard.models import Message
from scopeguard.llm import MockLLM
from scopeguard.changeorder import draftable
from scopeguard.rates import RateCard
SOW = open("examples/sow.md", encoding="utf-8").read()
def run(msgs, sow=SOW, tag=""):
    ms = [Message(id="", sender=s, text=t) for s, t in msgs]
    r = analyze(sow, None, Options(llm=MockLLM(), rates=RateCard(), client=["Dana"]) , messages=ms, sow_text=sow) if False else None
    from scopeguard.sow import parse_sow
    r = analyze(parse_sow(sow), None, Options(llm=MockLLM(), client=["Dana"]), messages=ms, sow_text=sow)
    for f in r.findings:
        d = f.status
        print(f"  [{d:5}] {f.cls:13} {f.rule or 'llm':16} {f.confidence:.2f} | {f.request.quote[:90]!r}")
    return r
def case(name, msgs, **k):
    print("##", name); return run(msgs, **k)
D = lambda *t: [("Dana", x) for x in t]
if __name__ == "__main__":
    case("negation far", D("The shop, which I said before we don't need, is not something to add right now. Please add the online shop.") )
    case("neg far2", D("About the online shop, which I mentioned earlier we really do not want, can you at least put a cart button on the home page?"))
    case("neg far3", D("Regarding the online shop we discussed: I said before that we won't be needing it, so skip it."))
    case("hedge blog", D("I guess we could add a blog, no rush, probably not."))
    case("hedge2", D("Could you add a blog? Probably not needed though, just thinking out loud."))
    case("cond", D("If the budget allows, maybe a members area where clients log in."))
    case("cond2", D("Could you add a Spanish version if we have money left?"))
    case("cond3", D("Should the budget stretch, please add an online shop."))
    case("sarcasm", D("Oh sure, let's add a blog, a shop and a booking system while we're at it, why not."))
    case("later withdraw", D("Please add a booking calendar to the Contact page.", "Thanks! Also, can you write the text for the services page?", "On reflection, forget the booking calendar and the services text."))
    case("later withdraw2", D("Please add an online shop.", "Hey, about that shop request from yesterday: scratch it please."))
    case("later withdraw3", D("Please add an online shop.", "Sorry, ignore my earlier message about the shop."))
    case("later withdraw4", D("Please add a blog section.", "The blog is off the table.", "Actually we are good without it."))
    case("qty words", D("Can the gallery show thirty photos?", "Can the gallery have a couple of photos more than 24?", "Please make the gallery hold forty images.","Add twenty-five photos to the gallery."))
    case("units per page", D("Please put 24 photos per page in the gallery.", "I want 30 photos in total across the gallery, 10 per page.","Can the gallery have 3 photos?"))
    case("cap other forms", D("Can you add 25 photos to the gallery?"), sow=SOW.replace("up to 24 client-supplied images","up to two dozen client-supplied images"))
    case("cap other forms2", D("Can the gallery have 30 images?"), sow=SOW.replace("up to 24 client-supplied images","a maximum of 24 images"))
    case("cap other forms3", D("Can the gallery have 30 images?"), sow=SOW.replace("up to 24 client-supplied images","max. 24 images"))
    case("cap other forms4", D("Can the gallery have 30 images?"), sow=SOW.replace("up to 24 client-supplied images","no more than 24 images"))
    case("pages max", D("Please add a Pricing page."), sow=SOW.replace("A five-page responsive website","A responsive website of max. 5 pages"))
    case("pages max2", D("Please add a Pricing page."), sow=SOW.replace("A five-page responsive website","A responsive website (up to 5 pages)"))
    case("pages max3", D("Please add a sixth page for pricing."), sow=SOW.replace("A five-page responsive website","A responsive website with 5 pages"))
