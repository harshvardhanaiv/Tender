"""Pre-market notice detection: a speculative / PIN / market-engagement notice is not a live tender."""
import sys

from tender_app.notice_kind import is_pre_market, clarification_rules, writer_rules, notice_steps

failed = 0


def check(name, cond):
    global failed
    print(("ok   " if cond else "FAIL ") + name)
    failed += 0 if cond else 1


carmarthen = ("Speculative Notice. Carmarthenshire County Council is currently preparing for the renewal of its "
              "Community Based Services Framework under Regulation 74. Market Engagement Event: invitation to attend a "
              "briefing session. Register by emailing HRosser@carmarthenshire.gov.uk")
check("the Carmarthenshire speculative notice is pre-market", is_pre_market("Community Based Services", carmarthen))
check("a prior information notice is pre-market", is_pre_market("Prior Information Notice for cleaning services"))
check("a preliminary market consultation is pre-market", is_pre_market("Preliminary market consultation for IT"))
check("a live ITT is not pre-market", not is_pre_market("Invitation to Tender: cleaning services", "Tenders must be submitted by 12:00"))
check("a notice that also invites tenders is not pre-market",
      not is_pre_market("Prior information notice", "Invitation to tender documents attached. Submission deadline: 12/01/2027"))
check("ordinary text is not pre-market", not is_pre_market("Supply of laptops", "Framework for 500 laptops"))
check("empty input is not pre-market", not is_pre_market(None, "", None))
check("the rules say 4 to 6 questions", "4 to 6 questions" in clarification_rules())
check("the writer rules ban invented pricing", "price schedule" in writer_rules())
# False positives measured on 15k real notices: these must stay live tenders
check("a PIN used as a call for competition is live", not is_pre_market("Open for tenders", fields=("Cleaning", "F01: Prior information notice (Call for competition)")))
check("a PIN without call for competition is pre-market", is_pre_market("Supply of cars", fields=("Cars", "F21: Social and other specific services (Prior information notice without call for competition)")))
check("a planned procurement notice type is pre-market", is_pre_market("Maintenance services", fields=("Fire safety", "UK3: Planned procurement notice")))
check("a UK4 tender notice that recalls a past engagement is live",
      not is_pre_market("Tender for software. A preliminary market engagement event was held last year to shape this requirement.", fields=("Housing software", "UK4")))
check("a contract award notice that mentions a PIN is not pre-market", not is_pre_market("Following our prior information notice we awarded...", fields=("All-Aged Carers", "UK6: Contract award notice")))
check("'PIN pads' in a title is not a prior information notice", not is_pre_market("Supply of chip and PIN pads for 200 shops", fields=("Chip and PIN pads", "")))
check("the type label decides when the body says ITT in the future tense",
      is_pre_market("The ITT is expected in December.", fields=("Care framework", "Prior information notice")))
steps = notice_steps(carmarthen, [{"event": "Briefing", "date": "03/12/2026", "action": True}])
check("the notice plan has no 'submit' steps and starts by saying so", steps[0].startswith("This is a notice, not a tender"))
check("the notice plan names the registration emails from the notice", "HRosser@carmarthenshire.gov.uk" in steps[1])
check("the notice plan carries the dated action", "03/12/2026" in steps[1])
check("the notice plan invents no email when the notice has none", "@" not in notice_steps("Speculative notice, no contact.")[1])
sys.exit(1 if failed else 0)
