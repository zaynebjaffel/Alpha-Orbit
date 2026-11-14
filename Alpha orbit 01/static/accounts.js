(() => {
  const workspace = document.getElementById("workspace");
  const accountView = document.getElementById("account-view");
  const content = document.getElementById("account-content");
  const nav = document.getElementById("account-actions");
  let currentUser = null;

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
  const dateText = (value) => value ? new Date(value).toLocaleDateString() : "—";
  const route = () => (location.hash || "#/").slice(1).split("?")[0] || "/";
  const navigate = (path) => { location.hash = path; };

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(`/api${path}`, { credentials: "same-origin", ...options, headers });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join(" ")
        : data.detail || data.message || "The request could not be completed.";
      throw new Error(detail);
    }
    return data;
  }

  function renderNav() {
    if (!currentUser) {
      nav.innerHTML = '<a href="#/">Map</a><a href="#/login">Sign in</a><a class="button primary" href="#/register">Create account</a>';
      return;
    }
    nav.innerHTML = `
      <a href="#/">Map</a>
      <a href="#/dashboard">Dashboard</a>
      <details class="account-menu">
        <summary>${escapeHtml(currentUser.first_name)} ▾</summary>
        <div class="account-menu-links">
          <a href="#/profile">My Profile</a>
          <a href="#/farms">My Farms</a>
          <a href="#/analyses">My Analyses</a>
          <a href="#/settings">Settings</a>
          <button class="button secondary" type="button" data-action="logout">Log out</button>
        </div>
      </details>`;
  }

  function messageBlock() {
    return '<p class="form-message" role="status" aria-live="polite"></p>';
  }

  function authCard(title, description, body, links = "") {
    content.innerHTML = `
      <section class="account-card narrow">
        <h2>${title}</h2><p>${description}</p>
        ${body}
        ${messageBlock()}
        ${links ? `<div class="account-links">${links}</div>` : ""}
      </section>`;
  }

  function showMessage(message, type = "") {
    const target = content.querySelector(".form-message");
    if (!target) return;
    target.textContent = message;
    target.className = `form-message ${type}`;
  }

  function loginPage() {
    authCard("Welcome back", "Sign in to manage your farms and saved analyses.", `
      <form class="account-form" data-form="login">
        <label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required /></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required /></label>
        <label><input name="remember_me" type="checkbox" /> Remember me for 30 days</label>
        <button class="button primary" type="submit">Sign in</button>
      </form>`,
    '<a class="text-link" href="#/forgot-password">Forgot password?</a><a class="text-link" href="#/register">Create account</a>');
  }

  function registerPage() {
    authCard("Create your account", "Save farm boundaries and revisit your Field Shift work.", `
      <form class="account-form" data-form="register">
        <div class="form-grid">
          <label>First name<input name="first_name" autocomplete="given-name" maxlength="80" required /></label>
          <label>Last name<input name="last_name" autocomplete="family-name" maxlength="80" required /></label>
        </div>
        <label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required /></label>
        <div class="form-grid">
          <label>Password<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /><span class="muted">Use at least 12 characters.</span></label>
          <label>Confirm password<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></label>
        </div>
        <div class="form-grid">
          <label>Country<input name="country" autocomplete="country-name" maxlength="100" required /></label>
          <label>Phone (optional)<input name="phone" type="tel" autocomplete="tel" maxlength="30" /></label>
        </div>
        <button class="button primary" type="submit">Create account</button>
      </form>`,
    '<a class="text-link" href="#/login">Already have an account? Sign in</a>');
  }

  function forgotPage() {
    authCard("Reset your password", "Enter your account email. If it exists, we’ll send a reset link.", `
      <form class="account-form" data-form="forgot">
        <label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required /></label>
        <button class="button primary" type="submit">Send reset link</button>
      </form>`,
    '<a class="text-link" href="#/login">Back to sign in</a>');
  }

  function resetPage() {
    const token = new URLSearchParams(location.hash.split("?")[1] || "").get("token") || "";
    authCard("Choose a new password", "Reset links expire after 30 minutes.", `
      <form class="account-form" data-form="reset">
        <label>New password<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></label>
        <label>Confirm new password<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></label>
        <input name="token" type="hidden" value="${escapeHtml(token)}" />
        <button class="button primary" type="submit">Update password</button>
      </form>`,
    '<a class="text-link" href="#/login">Back to sign in</a>');
  }

  async function dashboardPage() {
    const data = await api("/dashboard");
    const farms = data.farms.length
      ? `<div class="account-list">${data.farms.map((farm) => `
          <div class="account-item"><div><h3>${escapeHtml(farm.farm_name)}</h3>
          <p>${Number(farm.area_hectares).toFixed(2)} ha · added ${dateText(farm.created_at)}</p></div>
          <a class="button secondary" href="#/farms">View farms</a></div>`).join("")}</div>`
      : '<p class="muted">No farms saved yet. Draw a farm on the map, then save it in My Farms.</p>';
    content.innerHTML = `
      <section class="account-card">
        <h2>Welcome, ${escapeHtml(data.user.first_name)}</h2>
        <p>Your personal Field Shift workspace.</p>
        <div class="dashboard-stats">
          <div class="stat-card">Registered farms<strong>${data.farm_count}</strong></div>
          <div class="stat-card">Saved analyses<strong>${data.analysis_count}</strong></div>
        </div>
        <h2>Recent farms</h2>${farms}
        <div class="account-links"><a class="button primary" href="#/farms">Manage farms</a><a class="button secondary" href="#/analyses">View analyses</a></div>
      </section>`;
  }

  async function profilePage() {
    const data = await api("/profile");
    const user = data.user;
    content.innerHTML = `
      <section class="account-card">
        <h2>My Profile</h2><p>Manage your personal information and profile picture.</p>
        ${user.has_profile_picture ? `<img class="avatar" src="/api/profile/avatar?${Date.now()}" alt="Your profile picture" />` : ""}
        <form class="account-form" data-form="profile">
          <div class="form-grid">
            <label>First name<input name="first_name" maxlength="80" value="${escapeHtml(user.first_name)}" required /></label>
            <label>Last name<input name="last_name" maxlength="80" value="${escapeHtml(user.last_name)}" required /></label>
          </div>
          <label>Email address<input value="${escapeHtml(user.email)}" disabled /></label>
          <div class="form-grid">
            <label>Country<input name="country" maxlength="100" value="${escapeHtml(user.country)}" required /></label>
            <label>Phone<input name="phone" type="tel" maxlength="30" value="${escapeHtml(user.phone || "")}" /></label>
          </div>
          <p class="account-meta">Account created ${dateText(user.created_at)}</p>
          <button class="button primary" type="submit">Save profile</button>
        </form>
        <form class="account-form" data-form="avatar" enctype="multipart/form-data">
          <label>Profile picture<input name="file" type="file" accept="image/png,image/jpeg,image/webp" required /></label>
          <span class="muted">PNG, JPEG, or WebP; maximum 2 MB.</span>
          <button class="button secondary" type="submit">Upload picture</button>
        </form>
        ${messageBlock()}
      </section>`;
  }

  async function settingsPage() {
    content.innerHTML = `
      <section class="account-card narrow">
        <h2>Settings</h2><p>Change your account password. You’ll be signed out on other sessions.</p>
        <form class="account-form" data-form="password">
          <label>Current password<input name="current_password" type="password" autocomplete="current-password" required /></label>
          <label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></label>
          <label>Confirm new password<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required /></label>
          <button class="button primary" type="submit">Change password</button>
        </form>
        ${messageBlock()}
      </section>`;
  }

  async function farmsPage() {
    const data = await api("/farms");
    const featureAvailable = Boolean(window.fieldShift?.getLatestGeoJSON());
    const items = data.farms.length ? data.farms.map((farm) => `
      <article class="account-item">
        <div>
          <h3>${escapeHtml(farm.farm_name)}</h3>
          <p>${Number(farm.area_hectares).toFixed(2)} ha · ${Number(farm.centroid_latitude).toFixed(5)}, ${Number(farm.centroid_longitude).toFixed(5)}</p>
          <p>Created ${dateText(farm.created_at)}</p>
          <form class="account-item-actions" data-form="rename" data-id="${escapeHtml(farm.id)}">
            <input name="farm_name" aria-label="Farm name" maxlength="100" value="${escapeHtml(farm.farm_name)}" required />
            <button class="button secondary" type="submit">Rename</button>
            <button class="button secondary" type="button" data-action="replace-boundary" data-id="${escapeHtml(farm.id)}" ${featureAvailable ? "" : "disabled"}>Update boundary from map</button>
            <button class="button secondary" type="button" data-action="open-farm" data-id="${escapeHtml(farm.id)}">Open on map</button>
            <button class="button danger-button" type="button" data-action="delete-farm" data-id="${escapeHtml(farm.id)}">Delete</button>
          </form>
        </div>
      </article>`).join("") : '<p class="muted">No farms saved yet. Draw a polygon on the map and save it here.</p>';
    content.innerHTML = `
      <section class="account-card">
        <h2>My Farms</h2><p>Farm boundaries are private to your account.</p>
        <form class="account-form" data-form="new-farm">
          <label>Farm name<input name="farm_name" maxlength="100" required placeholder="e.g. North field" /></label>
          <button class="button primary" type="submit" ${featureAvailable ? "" : "disabled"}>Save current map boundary</button>
          ${featureAvailable ? "" : '<p class="muted">Draw a polygon on the map first. Your current map boundary will be saved.</p>'}
        </form>
        ${messageBlock()}
        <div class="account-list">${items}</div>
      </section>`;
  }

  async function analysesPage() {
    const [analysisData, farmData] = await Promise.all([api("/analyses"), api("/farms")]);
    const farms = farmData.farms.map((farm) => `<option value="${escapeHtml(farm.id)}">${escapeHtml(farm.farm_name)}</option>`).join("");
    const analyses = analysisData.analyses.length ? analysisData.analyses.map((analysis) => `
      <article class="account-item">
        <div><h3>${escapeHtml(analysis.selected_crop || "Farm analysis")} · ${escapeHtml(analysis.farm_name)}</h3>
          <p>${escapeHtml(analysis.climate_scenario || "No climate scenario")} · ${dateText(analysis.created_at)}</p>
          <details><summary>View saved results</summary><pre>${escapeHtml(JSON.stringify(analysis.analysis_results, null, 2))}</pre></details>
        </div>
      </article>`).join("") : '<p class="muted">No analysis results saved yet.</p>';
    content.innerHTML = `
      <section class="account-card">
        <h2>My Analyses</h2><p>Saved crop selections, results, and climate scenarios.</p>
        ${farmData.farms.length ? `
          <form class="account-form" data-form="analysis">
            <div class="form-grid">
              <label>Farm<select name="farm_id" required>${farms}</select></label>
              <label>Selected crop<input name="selected_crop" maxlength="100" /></label>
            </div>
            <label>Climate scenario<input name="climate_scenario" maxlength="100" placeholder="e.g. +2°C scenario" /></label>
            <label>Analysis results (JSON)<textarea name="analysis_results" required>{}</textarea></label>
            <button class="button primary" type="submit">Save analysis</button>
          </form>
          ${messageBlock()}` : '<p class="muted">Save a farm before recording an analysis.</p>'}
        <div class="account-list">${analyses}</div>
      </section>`;
  }

  async function renderRoute() {
    const activeRoute = route();
    const privateRoutes = ["/dashboard", "/profile", "/settings", "/farms", "/analyses"];
    if (privateRoutes.includes(activeRoute) && !currentUser) {
      navigate("/login");
      return;
    }
    const isWorkspace = activeRoute === "/" || activeRoute === "/map";
    workspace.hidden = !isWorkspace;
    accountView.hidden = isWorkspace;
    if (isWorkspace) {
      window.fieldShift?.invalidateMapSize();
      return;
    }
    try {
      switch (activeRoute) {
        case "/login":
          loginPage();
          break;
        case "/register":
          registerPage();
          break;
        case "/forgot-password":
          forgotPage();
          break;
        case "/reset-password":
          resetPage();
          break;
        case "/dashboard":
          await dashboardPage();
          break;
        case "/profile":
          await profilePage();
          break;
        case "/settings":
          await settingsPage();
          break;
        case "/farms":
          await farmsPage();
          break;
        case "/analyses":
          await analysesPage();
          break;
        default:
          workspace.hidden = false;
          accountView.hidden = true;
          window.fieldShift?.invalidateMapSize();
      }
    } catch (error) {
      content.innerHTML = `<section class="account-card"><p class="form-message error">${escapeHtml(error.message)}</p><a class="text-link" href="#/">Return to map</a></section>`;
    }
  }

  async function refreshUser() {
    try {
      currentUser = (await api("/auth/me")).user;
    } catch {
      currentUser = null;
    }
    renderNav();
  }

  async function handleForm(form) {
    const values = new FormData(form);
    const value = (name) => String(values.get(name) || "").trim();
    try {
      switch (form.dataset.form) {
        case "login": {
          const result = await api("/auth/login", {
            method: "POST",
            body: JSON.stringify({ email: value("email"), password: values.get("password"), remember_me: values.has("remember_me") }),
          });
          currentUser = result.user;
          renderNav();
          navigate("/dashboard");
          break;
        }
        case "register": {
          if (values.get("password") !== values.get("confirm_password")) throw new Error("Passwords do not match.");
          const result = await api("/auth/register", {
            method: "POST",
            body: JSON.stringify({
              first_name: value("first_name"),
              last_name: value("last_name"),
              email: value("email"),
              password: values.get("password"),
              country: value("country"),
              phone: value("phone") || null,
            }),
          });
          currentUser = result.user;
          renderNav();
          navigate("/dashboard");
          break;
        }
        case "forgot":
          showMessage((await api("/auth/forgot-password", {
            method: "POST",
            body: JSON.stringify({ email: value("email") }),
          })).message, "success");
          break;
        case "reset":
          if (values.get("password") !== values.get("confirm_password")) throw new Error("Passwords do not match.");
          await api("/auth/reset-password", {
            method: "POST",
            body: JSON.stringify({ token: values.get("token"), new_password: values.get("password") }),
          });
          showMessage("Password updated. You can now sign in.", "success");
          setTimeout(() => navigate("/login"), 1200);
          break;
        case "profile":
          currentUser = (await api("/profile", {
            method: "PATCH",
            body: JSON.stringify({
              first_name: value("first_name"),
              last_name: value("last_name"),
              country: value("country"),
              phone: value("phone") || null,
            }),
          })).user;
          renderNav();
          await profilePage();
          showMessage("Profile saved.", "success");
          break;
        case "avatar": {
          const file = values.get("file");
          const body = new FormData();
          body.append("file", file);
          await api("/profile/avatar", { method: "POST", body });
          await profilePage();
          showMessage("Profile picture updated.", "success");
          break;
        }
        case "password":
          if (values.get("new_password") !== values.get("confirm_password")) throw new Error("Passwords do not match.");
          await api("/profile/password", {
            method: "POST",
            body: JSON.stringify({ current_password: values.get("current_password"), new_password: values.get("new_password") }),
          });
          currentUser = null;
          renderNav();
          navigate("/login");
          break;
        case "new-farm":
          await api("/farms", {
            method: "POST",
            body: JSON.stringify({ farm_name: value("farm_name"), geojson: window.fieldShift.getLatestGeoJSON() }),
          });
          await farmsPage();
          showMessage("Farm saved to your account.", "success");
          break;
        case "rename":
          await api(`/farms/${encodeURIComponent(form.dataset.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ farm_name: value("farm_name") }),
          });
          await farmsPage();
          showMessage("Farm name updated.", "success");
          break;
        case "analysis": {
          let results;
          try {
            results = JSON.parse(value("analysis_results"));
          } catch {
            throw new Error("Analysis results must be valid JSON.");
          }
          if (!results || Array.isArray(results) || typeof results !== "object") throw new Error("Analysis results must be a JSON object.");
          await api("/analyses", {
            method: "POST",
            body: JSON.stringify({
              farm_id: value("farm_id"),
              selected_crop: value("selected_crop") || null,
              climate_scenario: value("climate_scenario") || null,
              analysis_results: results,
            }),
          });
          await analysesPage();
          showMessage("Analysis saved.", "success");
          break;
        }
      }
    } catch (error) {
      showMessage(error.message, "error");
    }
  }

  content.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    event.preventDefault();
    handleForm(form);
  });

  nav.addEventListener("click", async (event) => {
    if (!event.target.closest('[data-action="logout"]')) return;
    try {
      await api("/auth/logout", { method: "POST" });
      currentUser = null;
      renderNav();
      navigate("/login");
    } catch (error) {
      window.alert(error.message);
    }
  });

  content.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const farmId = button.dataset.id;
    try {
      if (button.dataset.action === "delete-farm") {
        if (!window.confirm("Delete this farm and its saved analyses?")) return;
        await api(`/farms/${encodeURIComponent(farmId)}`, { method: "DELETE" });
        await farmsPage();
        showMessage("Farm and its analyses deleted.", "success");
      } else if (button.dataset.action === "open-farm") {
        const farm = (await api(`/farms/${encodeURIComponent(farmId)}`)).farm;
        navigate("/");
        setTimeout(() => {
          try {
            window.fieldShift.openGeoJSON(farm.geojson);
          } catch (error) {
            window.alert(error.message);
          }
        }, 100);
      } else if (button.dataset.action === "replace-boundary") {
        const geojson = window.fieldShift.getLatestGeoJSON();
        if (!geojson) throw new Error("Draw or open a boundary on the map first.");
        await api(`/farms/${encodeURIComponent(farmId)}`, {
          method: "PATCH",
          body: JSON.stringify({ geojson }),
        });
        await farmsPage();
        showMessage("Farm boundary updated.", "success");
      }
    } catch (error) {
      showMessage(error.message, "error");
    }
  });

  window.addEventListener("hashchange", renderRoute);
  refreshUser().then(renderRoute);
})();
