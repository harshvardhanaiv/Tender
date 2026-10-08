/** Pricing / billing page — matches main app chrome */
(function () {
  const params = new URLSearchParams(location.search);
  const flash = document.getElementById("flash");

  let csrf = sessionStorage.getItem("tf_csrf") || "";
  let userPlan = "free";
  let subStatus = "trialing";
  let hasStripeCustomer = false;
  let subscriptionLegacy = false;
  let billingInterval = null;

  const PLAN_LABELS = {
    free: "Free",
    standard: "Solo",
    advance: "Teams",
    solo: "Solo",
    teams: "Teams",
    pack: "Pay as you go",
  };

  // This page is public. Reading state (/api/me) as an anonymous visitor is normal, so it
  // passes {anonymousOk: true}; anything that needs an account (checkout, billing portal)
  // sends the visitor to sign in instead.
  async function api(path, opts = {}) {
    const { anonymousOk, ...fetchOpts } = opts;
    const headers = { "Content-Type": "application/json", ...(fetchOpts.headers || {}) };
    if (csrf && fetchOpts.method && fetchOpts.method !== "GET") headers["X-CSRF-Token"] = csrf;
    const res = await fetch(path, { ...fetchOpts, headers });
    if (res.status === 401) {
      if (!anonymousOk) location.href = "/login.html";
      return null;
    }
    return res.json();
  }

  function planLabel(plan) {
    return PLAN_LABELS[plan] || plan;
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  function showAlert(message, title = "Alert") {
    return new Promise((resolve) => {
      const modal = document.getElementById("customAlertModal");
      const titleEl = document.getElementById("customAlertTitle");
      const msgEl = document.getElementById("customAlertMessage");
      const btnOk = document.getElementById("btnCustomAlertOk");
      
      if (!modal || !msgEl || !btnOk) {
        window.alert(message);
        resolve();
        return;
      }
      
      if (titleEl) titleEl.innerText = title;
      msgEl.innerHTML = esc(message).replace(/\n/g, "<br>");
      
      modal.classList.remove("hidden");
      
      const cleanup = () => {
        modal.classList.add("hidden");
        btnOk.removeEventListener("click", onOk);
        document.removeEventListener("keydown", onKey);
      };
      
      const onOk = () => {
        cleanup();
        resolve();
      };
      
      const onKey = (e) => {
        if (e.key === "Enter" || e.key === "Escape") {
          e.preventDefault();
          onOk();
        }
      };
      
      btnOk.addEventListener("click", onOk);
      document.addEventListener("keydown", onKey);
    });
  }

  function showConfirm(message, title = "Confirm") {
    return new Promise((resolve) => {
      const modal = document.getElementById("customConfirmModal");
      const titleEl = document.getElementById("customConfirmTitle");
      const msgEl = document.getElementById("customConfirmMessage");
      const btnOk = document.getElementById("btnCustomConfirmOk");
      const btnCancel = document.getElementById("btnCustomConfirmCancel");
      
      if (!modal || !msgEl || !btnOk || !btnCancel) {
        const res = window.confirm(message);
        resolve(res);
        return;
      }
      
      if (titleEl) titleEl.innerText = title;
      msgEl.innerHTML = esc(message).replace(/\n/g, "<br>");
      
      modal.classList.remove("hidden");
      
      const cleanup = () => {
        modal.classList.add("hidden");
        btnOk.removeEventListener("click", onOk);
        btnCancel.removeEventListener("click", onCancel);
        document.removeEventListener("keydown", onKey);
      };
      
      const onOk = () => {
        cleanup();
        resolve(true);
      };
      
      const onCancel = () => {
        cleanup();
        resolve(false);
      };
      
      const onKey = (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onOk();
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      };
      
      btnOk.addEventListener("click", onOk);
      btnCancel.addEventListener("click", onCancel);
      document.addEventListener("keydown", onKey);
    });
  }

  function showFlash(html, ok) {
    flash.innerHTML = html;
    flash.className = "pricing-flash " + (ok ? "pricing-flash--ok" : "pricing-flash--err");
    flash.style.display = "block";
  }

  async function confirmPayment(sessionId) {
    const data = await api("/api/billing/confirm", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (data?.ok) {
      let msg = `Payment successful! You now have ${data.balance} credits.`;
      if (data.invoice_pdf) {
        const btnInv = document.getElementById("btnDownloadInvoice");
        if (btnInv) {
          btnInv.href = data.invoice_pdf;
          btnInv.style.display = "inline-flex";
        }
      }
      showFlash(msg, true);
      updateBalanceUI(data.balance, data.plan);
      return true;
    }
    if (data?.error) showFlash(data.error, false);
    return false;
  }

  let catalogData = null;

  function updateBalanceUI(balance, plan) {
    const bal = document.getElementById("balanceAmt");
    const planEl = document.getElementById("planName");
    const navBal = document.getElementById("navCreditAmt");
    const subText = document.getElementById("creditsSubText");
    if (plan) userPlan = plan;
    if (bal) bal.textContent = balance;
    if (navBal) navBal.textContent = balance;
    if (planEl && plan) planEl.textContent = planLabel(plan);
    if (subText) {
      const pLabel = planLabel(plan || userPlan);
      subText.textContent = `${balance} credits available on the ${pLabel} plan`;
    }
    if (catalogData) {
      renderPlans();
    }
  }

  function normalizePlanName(p) {
    if (!p) return "free";
    const s = String(p).toLowerCase().trim();
    if (s === "advance" || s === "teams" || s === "pro") return "advance";
    if (s === "standard" || s === "solo") return "standard";
    return s;
  }

  function isCurrentPlan(p) {
    if (!userPlan || userPlan === "free" || p.mode !== "subscription") return false;
    const normUser = normalizePlanName(userPlan);
    const normCard = normalizePlanName(p.plan);
    return normUser === normCard && !subscriptionLegacy;
  }

  function subscriptionButton(p) {
    if (subscriptionLegacy && p.mode === "subscription") {
      return { label: "Cancel old plan first", disabled: true, current: false, action: "none" };
    }
    const isCurrent = isCurrentPlan(p);
    const hasPaidPlan = userPlan && userPlan !== "free" && !subscriptionLegacy;
    if (isCurrent) {
      return { label: "Current plan", disabled: true, current: true, action: "none" };
    }
    if (hasPaidPlan && p.mode === "subscription") {
      return { label: `Switch to ${p.label}`, disabled: false, current: false, action: "checkout" };
    }
    if (!p.available) {
      return { label: "Coming soon", disabled: true, current: false, action: "none" };
    }
    return { label: "Subscribe", disabled: false, current: false, action: "checkout" };
  }

  function planCard(p, featured) {
    const btn = subscriptionButton(p);
    const div = document.createElement("div");
    div.className = "plan-card"
      + (featured ? " plan-card--featured" : "")
      + (btn.current ? " plan-card--current" : "");
    
    if (!btn.current && !btn.disabled) {
      div.classList.add("plan-card--clickable");
      div.setAttribute("data-plan", p.id);
      div.setAttribute("data-action", btn.action);
    }

    // p.amount_gbp comes from /api/billing/catalog, which reads the live Stripe price by
    // price_id. Never hardcode a price here: a stale fallback shows customers a number we
    // will not charge. The struck-through "was" price is derived from the real one so the
    // "50% OFF" badge always agrees with the figures next to it.
    const hasPrice = Number.isFinite(Number(p.amount_gbp)) && Number(p.amount_gbp) > 0;
    const priceVal = hasPrice ? String(p.amount_gbp) : "—";
    // Double the price, keeping ".99" pricing: 49.99 -> 99.99 and 99.99 -> 199.99 (not 99.98 / 199.98),
    // matching the struck-through figures the in-app billing screen (app.js) shows.
    const cutPrice = (() => {
      if (!hasPrice) return "";
      const price = Number(p.amount_gbp);
      const endsIn99 = Math.abs((price % 1) - 0.99) < 0.005;
      const doubled = endsIn99 ? Math.round(price * 2) - 0.01 : price * 2;
      return (Math.round(doubled * 100) / 100).toString();
    })();

    const featuresList = p.plan === "standard" ? [
      `${p.credits} credits/month`,
      "Multi-portal search & live scraper",
      "AI fit score & tender analytics",
      "Standard email & deadline alerts"
    ] : [
      `${p.credits} credits/month`,
      "Unlimited multi-portal search",
      "Full AI proposal & bid writer suite",
      "Priority alerts & dedicated support"
    ];

    const featureItemsHtml = featuresList.map(f => `
        <li>
          <svg class="feature-checkbox" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="4" ry="4" fill="none" stroke="#22c55e"></rect>
            <path d="M9 12l2 2 4-4" stroke="#22c55e"></path>
          </svg>
          ${f}
        </li>`).join("");

    const buttonText = btn.label === "Subscribe" && hasPrice
      ? `Subscribe — £${priceVal}/mo + taxes`
      : btn.label;

    div.innerHTML = `
      <h3>
        ${p.label}
        <span class="plan-card__badge" style="background:#ef4444;color:#fff;font-weight:700;margin-left:4px;">50% OFF</span>
        ${btn.current ? '<span class="plan-card__badge">Current</span>' : (featured ? '<span class="plan-card__badge" style="background:#2563eb;color:#fff;">Popular</span>' : '')}
      </h3>
      <div class="plan-card__price">
        ${hasPrice ? `<span style="text-decoration: line-through; color: #94a3b8; font-size: 0.65em; font-weight: 500; margin-right: 6px;">£${cutPrice}</span>` : ""}
        ${hasPrice ? "£" : ""}${priceVal}<span>/mo + taxes</span>
      </div>
      <div class="plan-card__price-sub" style="color: #16a34a; font-weight: 600;">50% special discount &middot; Billed monthly</div>
      <ul class="plan-card__features" style="margin-bottom: 20px;">
        ${featureItemsHtml}
      </ul>
      <div style="margin-top: auto;">
        <button type="button" class="btn ${featured ? 'btn--primary' : 'btn--secondary'}" style="width: 100%; border-radius: 8px; font-weight: 600; padding: 10px 16px; cursor: pointer;" ${btn.disabled ? 'disabled' : ''}>
          ${buttonText}
        </button>
      </div>`;
    return div;
  }

  function packCard(p) {
    const div = document.createElement("div");
    div.className = "plan-card";
    if (p.available) {
      div.className += " plan-card--clickable";
      div.setAttribute("data-plan", p.id);
      div.setAttribute("data-action", "checkout");
    }
    div.innerHTML = `
      <h3>${p.label}</h3>
      <div class="plan-card__price">£${p.amount_gbp}</div>
      <ul class="plan-card__features">
        <li>
          <svg class="feature-checkbox" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="4" ry="4" fill="none" stroke="#22c55e"></rect>
            <path d="M9 12l2 2 4-4" stroke="#22c55e"></path>
          </svg>
          ${p.credits} credits added instantly
        </li>
        <li>
          <svg class="feature-checkbox" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="4" ry="4" fill="none" stroke="#22c55e"></rect>
            <path d="M9 12l2 2 4-4" stroke="#22c55e"></path>
          </svg>
          Works with any plan
        </li>
      </ul>
      ${!p.available ? '<div style="font-size:12px;color:var(--muted);margin-top:0.25rem">Coming soon</div>' : ""}`;
    return div;
  }

  function renderPlans() {
    const planGrid = document.getElementById("planGrid");
    if (!planGrid || !catalogData) return;
    planGrid.innerHTML = "";
    const subs = catalogData.subscriptions || [];
    const order = { standard: 1, advance: 2 };
    subs.sort((a, b) => (order[a.plan] || 9) - (order[b.plan] || 9));

    subs.forEach((p) => {
      const isFeatured = p.plan === "advance";
      planGrid.appendChild(planCard(p, isFeatured));
    });
  }

  async function openPortal() {
    const data = await api("/api/billing/portal", { method: "POST", body: "{}" });
    if (data?.url) location.href = data.url;
    else if (data?.error) await showAlert(data.error);
  }

  async function checkout(planId) {
    const data = await api("/api/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan: planId }),
    });
    if (data?.url) location.href = data.url;
    else if (data?.error) await showAlert(data.error);
  }

  async function cancelSubscription() {
    if (!await showConfirm("Cancel your subscription now? You can subscribe again immediately after.")) return;
    const data = await api("/api/billing/cancel", { method: "POST", body: "{}" });
    if (data?.ok) {
      showFlash("Subscription cancelled. Choose a plan below to subscribe again.", true);
      subscriptionLegacy = false;
      subStatus = "canceled";
      userPlan = "free";
      document.getElementById("legacyBanner").style.display = "none";
      document.getElementById("switchHint").style.display = "none";
      document.getElementById("btnPortal").style.display = "none";
      document.getElementById("planName").textContent = planLabel("free");
      renderPlans();
    } else if (data?.error) {
      showFlash(data.error, false);
    }
  }

  document.getElementById("btnCancelLegacy")?.addEventListener("click", cancelSubscription);
  document.getElementById("btnDismissLegacy")?.addEventListener("click", () => {
    document.getElementById("legacyBanner").style.display = "none";
  });

  document.getElementById("btnPortal")?.addEventListener("click", openPortal);

  document.getElementById("btnThemeToggle")?.addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    if (next === "light") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", "dark");
    try { localStorage.setItem("tf_theme", next); } catch (e) { /* ignore */ }
    document.getElementById("btnThemeToggle").textContent = next === "dark" ? "☀️" : "🌙";
  });

  if (params.get("success")) {
    const sessionId = params.get("session_id");
    showFlash(sessionId ? "Confirming payment…" : "Payment successful! Credits will appear shortly.", true);
  } else if (params.get("switched")) {
    showFlash("Subscription plan updated successfully!", true);
  } else if (params.get("downgraded")) {
    const pKey = params.get("plan") || "standard";
    const pName = planLabel(pKey);
    showFlash(`Plan downgrade scheduled! Your current plan remains active until the end of your current billing cycle. On your next billing date, your subscription will automatically renew on the ${pName} plan.`, true);
  } else if (params.get("cancelled")) {
    showAlert("Checkout process was cancelled. No charges were made to your account.\n\nYou can subscribe or purchase top-up credits whenever you are ready.", "Checkout Cancelled");
  }

  async function init() {
    const themeBtn = document.getElementById("btnThemeToggle");
    if (themeBtn && document.documentElement.getAttribute("data-theme") === "dark") {
      themeBtn.textContent = "☀️";
    }

    // Restore cached state instantly for 0ms render
    try {
      const cachedMe = JSON.parse(sessionStorage.getItem("tf_me_cache") || "null");
      const cachedCat = JSON.parse(sessionStorage.getItem("tf_catalog_cache") || "null");
      if (cachedMe) {
        userPlan = cachedMe.plan || "free";
        subStatus = cachedMe.subscription_status || "trialing";
        hasStripeCustomer = !!cachedMe.has_stripe_customer;
        subscriptionLegacy = !!cachedMe.subscription_legacy;
        updateBalanceUI(cachedMe.balance ?? 0, userPlan);
        document.getElementById("planName").textContent = planLabel(userPlan);
      }
      if (cachedCat) {
        catalogData = cachedCat;
        renderPlans();
      }
    } catch (e) { /* ignore cache errors */ }

    // Fetch user & billing catalog in parallel
    const mePromise = api("/api/me", { anonymousOk: true }).then(me => {
      if (me?.csrf_token) {
        csrf = me.csrf_token;
        sessionStorage.setItem("tf_csrf", csrf);
      }
      if (me?.ok) {
        try { sessionStorage.setItem("tf_me_cache", JSON.stringify(me)); } catch (e) {}
        userPlan = me.plan || "free";
        subStatus = me.subscription_status || "trialing";
        hasStripeCustomer = !!me.has_stripe_customer;
        subscriptionLegacy = !!me.subscription_legacy;
        billingInterval = me.billing_interval || null;
        document.getElementById("balanceBar").style.display = "block";
        updateBalanceUI(me.balance ?? 0, userPlan);
        document.getElementById("planName").textContent = planLabel(userPlan);
        if (subscriptionLegacy) {
          document.getElementById("legacyBanner").style.display = "block";
        } else if (subStatus === "active" || hasStripeCustomer) {
          document.getElementById("btnPortal").style.display = "inline-flex";
          document.getElementById("switchHint").style.display = "block";
        }
        if (me?.invoice_pdf) {
          const btnInv = document.getElementById("btnDownloadInvoice");
          if (btnInv) {
            btnInv.href = me.invoice_pdf;
            btnInv.style.display = "inline-flex";
          }
        }
        renderPlans();
      }
    });

    const catPromise = api("/api/billing/catalog").then(cat => {
      if (cat) {
        try { sessionStorage.setItem("tf_catalog_cache", JSON.stringify(cat)); } catch (e) {}
        catalogData = cat;
        renderPlans();
        const packGrid = document.getElementById("packGrid");
        if (packGrid) {
          packGrid.innerHTML = "";
          (cat.packs || []).forEach((p) => packGrid.appendChild(packCard(p)));
        }
      }
    });

    await Promise.all([mePromise, catPromise]);

    const sessionId = params.get("session_id");
    if (params.get("success") && sessionId) {
      await confirmPayment(sessionId);
    }

    document.body.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-plan]");
      if (!btn || btn.disabled) return;
      const action = btn.dataset.action || "checkout";
      if (action === "portal") openPortal();
      else if (action === "checkout") checkout(btn.dataset.plan);
    });
  }

  init();
})();
