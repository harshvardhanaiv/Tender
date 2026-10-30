/* Hover (and keyboard-focus) help for every button in the application. A button gets its text from, in this order:
   data-tip, its own title, the table for its id, the table for its data-action (plus data-decision, data-panel and
   data-filter, which tell apart buttons that share an action), the table for its visible label, and last its aria-label.
   Buttons are found when the page loads and whenever the screen changes, so buttons drawn later by the other scripts get
   one too. The text is shown in one small styled bubble (not the browser's slow, unstyled title), after a short pause so
   it never gets in the way; it is dropped on click, scroll, Escape and when the pointer leaves. A button with no entry
   simply has no help: nothing is invented for it. To give a new button help, add data-tip="..." to it, or an entry below.
   Plain text only (textContent), so nothing here can inject markup. English only, like the tips themselves. */
(function (root) {
  "use strict";

  // ── the words: by id (the fixed buttons of the page) ──────────────────────────────────────────
  const BY_ID = {
    btnUploadCustomTender: "Add a tender that is not on the portals (paste it or upload its documents) so you can summarise and bid for it.",
    btnLangDropdown: "Change the language TenderFlow is shown in.",
    btnProfileDropdown: "Your account: profile, colours, billing and sign out.",
    btnThemeToggle: "Switch between the light and the dark look.",
    btnTakeTour: "Replay the short guided tour of TenderFlow.",
    btnConnectionsNav: "Sign in to the apps TenderFlow works with, such as Outreach, without leaving this page.",
    btnLogout: "Sign out of TenderFlow on this device.",
    btnSelectAllCountyFilter: "Tick every county in the list.",
    btnClearCountyFilter: "Untick every county.",
    btnResetDate: "Remove the date filter.",
    btnSidebarSearch: "Run the search with the filters on the left.",
    btnSidebarStopSearch: "Stop the search that is running. Results found so far stay on screen.",
    chipProfile: "Choose which company profile tenders are matched against.",
    chipCountries: "Choose which countries to search.",
    chipPortals: "Choose which tender portals to search.",
    chipPublished: "Only show tenders published in a date range.",
    chipValue: "Only show tenders within a contract value range.",
    tasksPanelToggle: "Show or hide the list of summaries, analyses and bids running in the background.",
    btnCloseModal: "Close the analysis window.",
    btnRunAnalysis: "Look through the tender documents for gaps and risks and draft questions to put to the buyer.",
    btnReRunSummary: "Write the tender summary again.",
    btnReRunAnalysis: "Run the clarification analysis again.",
    btnCloseBidModal: "Close the bid response window.",
    btnManageProfiles: "Open your company profile to check or change its details.",
    btnBidAddUserDoc: "Attach one of your own documents for the response to draw on.",
    btnOpenAnswerBank: "Open your Answer Bank of reusable answers.",
    btnRunBidAssistant: "Have the AI draft a response to this tender from your company profile and answers.",
    btnReRunBidAssistant: "Draft the response again.",
    btnPauseBidAssistant: "Pause the drafting. You can carry on later.",
    btnStopBidAssistant: "Stop drafting the response.",
    cpClose: "Close company profiles.",
    cpNewProfileBtn: "Create another company profile, for a different business or a different kind of bid.",
    cpRegistryGb: "Look the company up in the UK register (Companies House).",
    cpRegistryIe: "Look the company up in the Irish register.",
    cpLookupBtn: "Look the company up in the chosen register and fill in its details.",
    cpEnrichBtn: "Add more details (website, contacts, size) from public sources.",
    cpEnterDetailsBtn: "Type the profile yourself instead of using the AI.",
    cpGenBtn: "Have the AI write the profile text from the company's details.",
    cpBackToAiBtn: "Go back to the AI generator.",
    cpEnrichDunsBtn: "Find the company's D-U-N-S number from public sources.",
    cpEnrichContactsBtn: "Find contact details for the company from public sources.",
    cpEnrichAiContextBtn: "Have the AI add the company's relevant experience to the profile.",
    cpCancelBtn: "Close without saving.",
    cpBackBtn: "Go back one step.",
    cpNextBtn: "Go to the next step, or save the profile on the last one.",
    btnCloseHistoryModal: "Close history.",
    btnCloseDashboard: "Close the pipeline.",
    btnFundingFilters: "Show the grant filters.",
    btnFundingClear: "Remove every filter and show all grants.",
    abClose: "Close the Answer Bank.",
    abNewBtn: "Write a new reusable answer.",
    abFormClose: "Close without saving.",
    abToggleQModeBtn: "Switch between typing your own question and choosing a standard bid question.",
    abFormAi: "Have the AI draft this answer from your company profile.",
    abFormEnrich: "Have the AI improve and expand the answer you have written.",
    abFormCancel: "Close without saving.",
    abFormSave: "Save this answer to your Answer Bank.",
    btnCreditsBanner: "Buy more credits.",
    btnDismissLowCredit: "Hide this reminder for now.",
    btnCreditsModalGo: "See the plans and credit packs.",
    btnCreditsModalClose: "Close.",
    btnPricingPortal: "Open the billing portal to change or cancel your subscription.",
    btnPricingModalClose: "Close.",
    profileClose: "Close My Profile.",
    btnCloseColorPicker: "Close the colour picker.",
    btnToggleAiKey: "Show or hide the key you typed.",
    btnAddRecipientEmail: "Add another address that receives tender alerts.",
    btnTestEmailAlert: "Send one test alert to your addresses now. This really sends an email.",
    eoPreview: "Show what would be sent through Outreach. Nothing is sent.",
    eoSend: "Send the message through Outreach.",
    eoRefresh: "Check the Outreach connection and mailbox again.",
    btnSaveProfileGlobal: "Save your profile and settings.",
    supplierIntelClose: "Close supplier intelligence.",
    chipSupplierDates: "Only show contracts awarded in a date range.",
    chipSupplierDistance: "Only show suppliers within a distance of a place.",
    btnResetSupplierFilter: "Remove every supplier filter.",
    tabBtnSupplierList: "Show the suppliers as a list.",
    tabBtnSupplierMap: "Show the suppliers on a map.",
    planningLeadsClose: "Close planning leads.",
    chipPlanningDwellings: "Only show applications for a number of homes.",
    chipPlanningDates: "Only show applications submitted in a date range.",
    chipPlanningAuthority: "Choose which councils to include.",
    chipPlanningDistance: "Only show applications within a distance of a place.",
    btnPlanningClear: "Remove every planning filter.",
    btnPlanningViewList: "Show the applications as a list.",
    btnPlanningViewMap: "Show the applications on a map.",
    btnPlanningPrev: "Go to the previous page of results.",
    btnPlanningNext: "Go to the next page of results.",
    btnPlanningSearchArea: "Look for applications in the part of the map you can see now.",
    growthStudioClose: "Close Growth Studio.",
    chipGsMore: "Show more filters.",
    abChecklistBtn: "See which documents most tenders ask for you already have, and which are missing.",
    gsServicesBtn: "Choose one or more services you sell. Signals match any of them.",
    gsServicesSave: "Save this service to your list and select it.",
    gsServicesCustom: "Search by a CPV code or words in the contract title instead.",
    gsTabSignals: "Buyers whose contracts are ending, planning approvals and market engagements in your category.",
    gsTabCampaigns: "Your outreach campaigns.",
    gsTabBuilder: "Write and send the email for the campaign you are building.",
    gsTabSocial: "Write social posts with the AI or by hand, preview them, then approve and schedule them.",
    gsTabCalls: "Have Outreach's AI phone one buyer at a time.",
    gsTabInbox: "The emails you sent through Outreach and the replies to them.",
    gsTabPerformance: "How your outreach is doing: emails sent, replies and meetings.",
    buyerIntelClose: "Close buyer intelligence.",
    buyerDrawerClose: "Close the buyer's details.",
    supplierSummaryClose: "Close the supplier summary.",
    userDocumentsClose: "Close your documents.",
    docPreviewClose: "Close the preview.",
    btnCustomAlertOk: "Dismiss this message.",
    btnCustomConfirmCancel: "Cancel. Nothing changes.",
    btnCustomConfirmOk: "Go ahead.",
    wtSkip: "End the tour.",
    wtBack: "Go back one step of the tour.",
    wtNext: "Go to the next step of the tour.",
  };

  // ── by data-action (Growth Studio, Outreach, buyer workspace) ─────────────────────────────────
  const BY_ACTION = {
    "apply-custom": "Use the category you typed.",
    "back-to-campaigns": "Go back to your list of campaigns.",
    "clear-selection": "Untick every signal.",
    "copy-mailchimp": "Copy the message so you can paste it into Mailchimp.",
    "create-from-selection": "Start a campaign from the signals you ticked.",
    "delete-campaign": "Delete this campaign and its list of buyers. Emails already sent are not unsent.",
    dismiss: "Hide this signal. You can bring it back from the dismissed list.",
    "draft-ai": "Have the AI write the subject and message from your company profile.",
    "draft-template": "Fill in a ready-made message you can then edit.",
    export: "Download the buyers and the message as a CSV file.",
    "focus-email": "Jump to the address box for this buyer.",
    "goto-signals": "Go to the Signals tab to pick buyers.",
    "goto-step": "Jump to this part of the campaign.",
    "rich-cmd": "Format the selected text in the message.",
    "attach-files": "Attach files to every email in this campaign. They are sent from your own email account.",
    "remove-attachment": "Take this file off the campaign. Nothing is deleted from your computer.",
    "insert-field": "Put this merge field in the message. It becomes each buyer's own detail when sent.",
    "mark-sent": "Record that these buyers have been emailed. This sends nothing.",
    "open-campaign": "Open this campaign.",
    "outreach-check": "Check the Outreach connection again.",
    "outreach-refresh": "Ask Outreach what became of the emails.",
    "outreach-send": "Send the message to every ticked buyer that has an address.",
    "pick-category": "Show signals for this category.",
    "reload-campaigns": "Try loading the campaigns again.",
    "reload-performance": "Try loading the figures again.",
    "reload-signals": "Try loading the signals again.",
    "remove-suppression": "Take this buyer off your do-not-contact list. Only do this if they have said it is fine.",
    "remove-target": "Remove this buyer from the campaign.",
    "reset-filters": "Clear every filter.",
    restore: "Bring this dismissed signal back.",
    "save-message": "Save the subject and message as you have edited them.",
    "start-campaign": "Start an email campaign for this buyer.",
    "preview-buyer": "Show the message this buyer would receive.",
    "preview-step": "Step to the previous or next buyer's message.",
    "buyer-filter": "Show only the buyers in this group.",
    "builder-tab": "Switch to this step of the campaign.",
    "toggle-dismissed": "Show or hide the signals you dismissed.",
    "toggle-fit": "Show or hide why this signal fits your company.",
    "toggle-more": "Show more of the list.",
    "use-suggestion": "Put this named contact's address in the box.",
    // Social and Calls
    "ch-call-details": "Show the answers and transcript of this call.",
    "ch-call-end": "Hang up this call now.",
    "ch-call-make": "Place the call now. The AI rings this number straight away.",
    "ch-call-refresh": "Check Outreach for the latest results of your calls.",
    "ch-lookup": "Look for a phone number on this buyer's notices.",
    "ch-open-profiles": "Open Company profiles to add or change your company's details.",
    "ch-picture-clear": "Remove the picture you chose.",
    "ch-edit-picture-clear": "Remove the picture you chose.",
    "ch-post-caption": "Save your edited caption. The post then needs approving again.",
    "ch-post-create": "Create the post: the AI writes it, or your own caption is used. Nothing is published until you approve it.",
    "ch-post-picture": "Put this picture on the post. The post then needs approving again.",
    "ch-post-refresh": "Check Outreach for the latest state of your posts.",
    "ch-post-reuse": "Copy this post's wording into the composer to write a new post from it.",
    "ch-post-rewrite": "Ask the AI to write the chosen part again, following your note.",
    "ch-post-select": "Show this post in the preview.",
    "ch-post-time": "Change when the post goes out. It then needs approving again.",
    // Inbox
    "in-delete": "Delete from your inbox. Outreach keeps its own record and the email stays sent. You can undo straight after.",
    "in-meeting": "Record that this buyer booked a meeting.",
    "in-more": "Load older emails.",
    "in-open": "Open to read the reply and see the email you sent.",
    "in-optout": "Never email this buyer again from Growth Studio.",
    "in-refresh": "Check Outreach for new replies.",
    "in-search": "Search your emails by address or subject.",
    "in-undo": "Bring the deleted email back.",
    // buyer workspace and market engagement
    checkout: "Buy this pack. You are taken to a secure payment page first.",
    "company-profile": "Open this supplier's company profile.",
    "cost-basis": "Choose which contract value the figures use.",
    "eng-add-note": "Add this note to the engagement's record.",
    "eng-add-supplier": "Add this supplier to the engagement list.",
    "eng-close": "Close the engagement once the response deadline has passed.",
    "eng-confirm-delete": "Delete the engagement for good.",
    "eng-convert": "Record that the engagement has been handed over to the tender.",
    "eng-copy-notice": "Copy the notice text.",
    "eng-create": "Create the plan.",
    "eng-delete": "Delete this engagement.",
    "eng-download-notice": "Download the notice as a text file.",
    "eng-handoff": "Download a hand-over pack for the tender team.",
    "eng-match": "Suggest suppliers from the buyer's award history.",
    "eng-new": "Plan a new market engagement.",
    "eng-publish": "Record that the notice has been published.",
    "eng-regenerate": "Have the AI write the notice again from the scope.",
    "eng-remove-supplier": "Remove this supplier from the list.",
    "eng-save-notice": "Save the notice as a draft.",
    "eng-save-scope": "Save the scope.",
    "modal-close": "Close without making a change.",
    "org-clear": "Stop using this organisation.",
    "org-use": "Use this organisation.",
    "peer-insights": "Show what this peer buyer buys and from whom.",
    "peer-toggle": "Show or hide this peer's contracts.",
    "peers-page": "Go to this page of peer buyers.",
    "plan-from": "Start a market engagement plan from this contract.",
    "radar-clear": "Remove the chosen category.",
    "radar-follow": "Follow or stop following this category.",
    "radar-pick": "Choose this category.",
    "radar-plan": "Plan a market engagement for this category.",
    "similar-suppliers": "Find suppliers similar to this one.",
    unfollow: "Stop following this category.",
    "not-awarded-more": "Show more contracts.",
    retry: "Try again.",
  };

  // buttons that share one action are told apart by the value that says which one they are
  const BY_DECISION = {
    approve: "Publish this post. You are asked to confirm first.",
    reject: "Reject this post. It will not be published.",
    cancel: "Cancel this post so it is never published.",
  };
  const BY_PANEL = {
    approve: "Approve, reject or cancel this post.",
    edit: "Edit the caption by hand, or use your own picture.",
    ai: "Ask the AI to improve or rewrite the text or the picture.",
    time: "See and change when the post goes out.",
  };
  const BY_FILTER = {
    replies: "Emails a buyer has replied to.",
    sent: "Emails that went out, whether or not there has been a reply.",
    bounced: "Emails that could not be delivered.",
    all: "Every email you sent through Outreach.",
  };

  // ── by visible label (buttons the scripts draw), compared without emoji, symbols or capitals ──
  const BY_LABEL = {
    en: "Show TenderFlow in English.", fr: "Show TenderFlow in French.", nl: "Show TenderFlow in Dutch.",
    "7d": "Published in the last 7 days.", "14d": "Published in the last 14 days.", "30d": "Published in the last 30 days.", "60d": "Published in the last 60 days.",
    anytime: "Do not filter by publication date.",
    "custom range": "Pick your own start and end dates.",
    all: "Show everything, with no filter.",
    opportunities: "Tenders that are still open for bids.", awarded: "Contracts that have already been awarded.",
    analysis: "Only the clarification analyses.", summary: "Only the tender summaries.", proposal: "Only the bid responses.",
    cancel: "Close without saving.", close: "Close this window.", ok: "Close this message.", dismiss: "Hide this for now.",
    clear: "Remove the filter.", "clear all": "Remove every filter.", "reset all": "Remove every filter.", "reset filters": "Remove every filter.",
    search: "Run the search.", stop: "Stop what is running.", pause: "Pause. You can resume later.", resume: "Carry on from where it stopped.",
    retry: "Try again.", "try again": "Try again.", "retry analysis": "Run the analysis again.",
    remove: "Remove this.", delete: "Delete this.", view: "Open this.", open: "Open this.", "open details": "Open the full details.", add: "Add this.",
    copy: "Copy to the clipboard.", "show more": "Show more.", "show details": "Show the details.", "view full profile": "Open the full profile.",
    first: "Go to the first page.", prev: "Go to the previous page.", previous: "Go to the previous page.", next: "Go to the next page.", last: "Go to the last page.",
    "first prev": "Go to the first page.",
    "save to answer bank": "Keep this as a reusable answer in your Answer Bank.",
    enrich: "Have the AI improve and add to this.",
    "download full proposal docx": "Download the whole proposal as a Word file.",
    "download docx": "Download this as a Word file.",
    "download full bid docx": "Download the whole bid as a Word file.",
    "stage 1 docx": "Download stage 1 as a Word file.", "stage 2 docx": "Download stage 2 as a Word file.", "stage 4 docx": "Download stage 4 as a Word file.",
    "stage 1 dissection": "Show the tender broken down into its requirements.",
    "stage 2 checklist": "Show the compliance checklist.",
    "stage 3 bid draft": "Show the drafted bid response.",
    "stage 4 clarifications": "Show the questions to put to the buyer.",
    "go to stage 4 clarifications": "Jump to the questions to put to the buyer.",
    "copy draft": "Copy the draft to the clipboard.", "copy letter": "Copy the letter to the clipboard.",
    "view buyer profile": "Open this buyer's profile.", "view supplier playbook": "Open the playbook for winning against this supplier.",
    "buyer intel": "Open buyer intelligence for this buyer.", "supplier intel": "Open supplier intelligence for this supplier.",
    "run in background": "Carry on while this runs; you are told when it is done.",
    "manage in profile": "Open your profile settings.",
    track: "Add this tender to your pipeline.",
    "view in pipeline": "Open this tender in your pipeline.",
    "search together": "Search with these choices combined.", "search suppliers": "Look for suppliers that match.",
    "create company profile": "Set up a company profile so tenders can be matched to you.",
    "create your first profile": "Set up a company profile so tenders can be matched to you.",
    "add your first answer": "Write your first reusable answer.",
    "new category": "Add a category of your own.",
    continue: "Go on to the next step.",
    "update dates": "Change the dates for this tender.", "save dates": "Save these dates.", "auto detect from file": "Read the dates from the uploaded document.",
    credits: "Add or remove credits for this user.",
    "bd playbook": "Show the business-development playbook for this supplier.", "generate bd playbook": "Have the AI write a playbook for winning against this supplier.",
    "ai summary": "Have the AI summarise this.",
    profile: "Open the profile.",
    "add phase": "Add a phase to the plan.", "ai re suggest": "Ask the AI for a different suggestion.",
    svg: "Download as an SVG image.", png: "Download as a PNG image.",
    "save changes": "Save your changes.", "generate diagram": "Have the AI draw the diagram.",
    "build gantt": "Draw a Gantt chart from the plan.", "methodology studio": "Open the methodology diagram editor.",
    "verify code": "Check the code you were sent.", resend: "Send the code again.",
    "create plan": "Create the plan.", "save scope": "Save the scope.", "find suppliers from award history": "Suggest suppliers from the buyer's award history.",
    "save draft": "Save the notice as a draft.", "download txt": "Download as a text file.", "mark as published": "Record that the notice has been published.",
    "add note": "Add this note to the record.", "close the engagement": "Close the engagement.",
    "download hand over pack md": "Download a hand-over pack for the tender team.",
    "mark as handed over to the tender": "Record that the engagement has been handed over to the tender.",
    "delete this engagement": "Delete this engagement.", "keep it": "Keep it. Nothing is deleted.",
    "new market engagement": "Plan a new market engagement.", "company profile": "Open this supplier's company profile.",
    "view similar suppliers": "Find suppliers similar to this one.", contracts: "Show or hide this buyer's contracts.", insights: "Show what this peer buys and from whom.",
    "plan a market engagement": "Plan a market engagement for this category.", "plan engagement": "Start a market engagement plan from this contract.",
    "stop following": "Stop following this category.", "use this organisation": "Use this organisation.",
    "annual value": "Base the figures on the yearly value of contracts.", "total contract value": "Base the figures on the whole value of contracts.",
    "see plans": "See the plans.",
    "set up your company": "Open Company profiles to add your company.", "open company profiles": "Open Company profiles.",
    "approve and publish": "Publish this post. You are asked to confirm first.", "approve for the new time": "Publish this post at the new time. You are asked to confirm first.",
    reject: "Reject this post. It will not be published.", "cancel post": "Cancel this post so it is never published.",
    "start a new post from this": "Copy this post's wording into the composer to write a new post from it.",
    "end call": "Hang up this call now.",
    "meeting booked": "Record that this buyer booked a meeting.", "add to do not contact": "Never email this buyer again from Growth Studio.",
    undo: "Bring the deleted email back.", restore: "Bring this back.",
    fit: "Show or hide why this signal fits your company.",
    "create campaign": "Start a campaign from the signals you ticked.", "clear selection": "Untick every signal.", "open": "Open this.",
    "use this address": "Put this named contact's address in the box.", "add one": "Go to the address box for this buyer.",
    "delete campaign": "Delete this campaign and its list of buyers.", "mark all as sent": "Record that every included buyer has been emailed. This sends nothing.",
    "add buyers from signals": "Go back to Signals to pick more buyers.",
    "save message": "Save the subject and message as you have edited them.", "draft from template": "Fill in a ready-made message you can then edit.",
    "export csv": "Download the buyers and the message as a CSV file.", "mailchimp audience csv": "Download the buyers as a CSV file to import into Mailchimp.",
    "copy message for mailchimp": "Copy the message so you can paste it into Mailchimp.",
    "i have connected check again": "Check the Outreach connection again.", "add addresses": "Go to the address boxes to add the missing ones.",
    "refresh status": "Ask Outreach what became of the emails.", refresh: "Look again for the latest.", "send mail": "Send the message to every ticked buyer that has an address.",
    preview: "Show what would be sent. Nothing is sent.", send: "Send the message.",
    "connect outreach": "Sign in to Outreach, or create an account, in the Connections window on this page.", "add an email account": "Set up your own email account in the Connections window on this page.", "email account settings": "Change your email account, send a test email or disconnect it.", "connect email account": "Check the login with your mail server and keep this email account.", "send test email": "Send a short test email from your email account to itself, to check that it lands.",
  };

  const CLOSE_SYMBOLS = /^[×✕✖xX]$|^&times;$/;
  const SELECTOR = 'button, [role="button"], [role="tab"], summary, a.gs-btn, a.btn, a.bw-btn, input[type="button"], input[type="submit"]';

  // letters and digits only, lower case: "🗂 Save to Answer Bank" -> "save to answer bank"
  const norm = (s) => String(s || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim().slice(0, 300);
  const own = (map, key) => (key != null && Object.prototype.hasOwnProperty.call(map, key) ? map[key] : "");

  // What this button's help says: "" when nothing is known (then it has none).
  function resolve(el) {
    if (!el || !el.getAttribute) return "";
    const attr = (n) => el.getAttribute(n);
    const ds = el.dataset || {};
    const title = clean(attr("title"));
    if (title) return title;
    const written = clean(attr("data-tip"));
    if (written && attr("data-tip-auto") !== "1") return written; // written by hand in the markup: never replaced by a table entry
    const action = attr("data-action");
    return (
      clean(own(BY_ID, el.id)) ||
      (action === "ch-post-decide" ? clean(own(BY_DECISION, ds.decision)) : "") ||
      (action === "ch-panel" ? clean(own(BY_PANEL, ds.panel)) : "") ||
      (action === "in-filter" ? clean(own(BY_FILTER, ds.filter)) : "") ||
      clean(own(BY_ACTION, action)) ||
      labelTip(el, attr) ||
      clean(attr("aria-label"))
    );
  }
  function labelTip(el, attr) {
    const text = clean(el.textContent) || clean(attr("value"));
    const aria = clean(attr("aria-label"));
    if (CLOSE_SYMBOLS.test(text) || /^close\b/i.test(aria)) return "Close this.";
    return clean(own(BY_LABEL, norm(text))) || clean(own(BY_LABEL, norm(aria)));
  }

  // Gives one element its help: data-tip holds it, the slow native title is dropped, and an icon-only button keeps an accessible name.
  function apply(el) {
    if (el.classList && el.classList.contains("visually-hidden")) return false; // not on screen, so nothing to hover
    const tip = resolve(el);
    if (!tip) return false;
    // a tip that came from a table is marked, so it is looked up again if the button's action or label changes
    const fromTable = !clean(el.getAttribute("title")) && !(clean(el.getAttribute("data-tip")) && el.getAttribute("data-tip-auto") !== "1");
    if (el.getAttribute("data-tip") !== tip) el.setAttribute("data-tip", tip);
    if (fromTable) el.setAttribute("data-tip-auto", "1");
    else if (el.hasAttribute("data-tip-auto")) el.removeAttribute("data-tip-auto");
    if (el.hasAttribute("title")) el.removeAttribute("title");
    if (!norm(el.textContent) && !el.getAttribute("aria-label") && !el.getAttribute("aria-labelledby")) el.setAttribute("aria-label", tip.replace(/\.$/, ""));
    return true;
  }

  const api = { resolve, apply, norm, SELECTOR, BY_ID, BY_ACTION, BY_LABEL };
  root.TFTips = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ── finding buttons, now and later ────────────────────────────────────────────────────────────
  function scan(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.id === "tfTip" || (node.closest && node.closest("[data-no-tip]"))) return;
    if (node.matches && node.matches(SELECTOR)) apply(node);
    if (node.querySelectorAll) node.querySelectorAll(SELECTOR).forEach(apply);
  }
  const pending = new Set();
  let scheduled = false;
  function flush() {
    scheduled = false;
    pending.forEach(scan);
    pending.clear();
  }
  function queue(node) {
    pending.add(node);
    if (!scheduled) {
      scheduled = true;
      (root.requestAnimationFrame || root.setTimeout)(flush, 16);
    }
  }
  function start() {
    scan(document.body);
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === "attributes") queue(m.target);
        else m.addedNodes.forEach((n) => { if (n.nodeType === 1) queue(n); });
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["title", "data-action", "data-decision", "data-panel", "data-filter"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  // ── the bubble ────────────────────────────────────────────────────────────────────────────────
  let bubble = null;
  let current = null;
  let timer = null;
  const canHover = () => !root.matchMedia || root.matchMedia("(hover: hover)").matches;

  function ensure() {
    if (bubble) return bubble;
    bubble = document.createElement("div");
    bubble.id = "tfTip";
    bubble.className = "tf-tip";
    bubble.setAttribute("role", "tooltip");
    bubble.hidden = true;
    document.body.appendChild(bubble);
    return bubble;
  }
  function place(el) {
    const r = el.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    bubble.style.left = "0px";
    bubble.style.top = "0px";
    const w = bubble.offsetWidth;
    const h = bubble.offsetHeight;
    let top = r.top - h - 8;
    let side = "above";
    if (top < 8) { top = r.bottom + 8; side = "below"; }
    if (top + h > vh - 4) top = Math.max(4, vh - h - 4);
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), Math.max(8, vw - w - 8));
    bubble.style.left = Math.round(left) + "px";
    bubble.style.top = Math.round(top) + "px";
    bubble.setAttribute("data-side", side);
  }
  function show(el) {
    const text = el.getAttribute && el.getAttribute("data-tip");
    if (!text || !document.contains(el)) return;
    ensure();
    bubble.textContent = text;
    bubble.hidden = false;
    place(el);
    if (current && current !== el) current.removeAttribute("aria-describedby");
    el.setAttribute("aria-describedby", "tfTip");
    current = el;
  }
  function hide() {
    clearTimeout(timer);
    timer = null;
    if (bubble) bubble.hidden = true;
    if (current) { current.removeAttribute("aria-describedby"); current = null; }
  }
  const tipTarget = (node) => (node && node.closest ? node.closest("[data-tip]") : null);

  document.addEventListener("mouseover", (e) => {
    if (!canHover()) return;
    const el = tipTarget(e.target);
    if (!el || el === current) return;
    if (current && !el.contains(current)) hide();
    clearTimeout(timer);
    timer = setTimeout(() => show(el), 350);
  });
  document.addEventListener("mouseout", (e) => {
    const el = tipTarget(e.target);
    if (!el) return;
    const to = e.relatedTarget;
    if (to && el.contains(to)) return;
    hide();
  });
  document.addEventListener("focusin", (e) => {
    const el = tipTarget(e.target);
    if (el && el.matches && el.matches(":focus-visible")) { clearTimeout(timer); show(el); }
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); }, true);
  document.addEventListener("scroll", hide, true);
  root.addEventListener("resize", hide);
})(typeof window !== "undefined" ? window : globalThis);
