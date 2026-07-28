"use strict";

const fsLog = require("fs");
const PANEL_LOG = "C:/Users/Floki/Desktop/D1 Retro/logs/panels.log";
function logPanel(tag, data) {
  try {
    fsLog.appendFileSync(
      PANEL_LOG,
      `${new Date().toISOString()} ${tag} ${JSON.stringify(data)}\n`,
    );
  } catch (e) {
    /* la journalisation ne doit jamais casser le panneau */
  }
}

// ── Pont local pour l'automatisation (ajout) ─────────────────────────────
// Le bot ne signe rien lui-même : il demande au client d'émettre la requête.
// Le serveur reçoit donc exactement ce qu'il recevrait d'un clic.
const httpBridge = require("http");
const BRIDGE_PORT = 8790;
let scopedApi = null; // capturé au premier vrai appel de panneau

const bridgeServer = httpBridge.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    const reply = (code, obj) => {
      res.writeHead(code, { "Content-Type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    if (req.method !== "POST") return reply(405, { error: "POST attendu" });
    if (!scopedApi) return reply(503, { error: "NON_AMORCE" });
    let p;
    try {
      p = JSON.parse(body || "{}");
    } catch (e) {
      return reply(400, { error: "JSON invalide" });
    }
    if (!p.panelId || !p.action)
      return reply(400, { error: "panelId et action requis" });
    try {
      const result = await scopedApi.post(
        `/api/panels/${encodeURIComponent(p.panelId)}/action`,
        { action: p.action, params: p.params || {} },
      );
      logPanel("BOT", {
        panelId: p.panelId,
        action: p.action,
        params: p.params,
        result,
      });
      reply(200, result);
    } catch (e) {
      reply(500, { error: String(e && e.message) });
    }
  });
});

// Sans ce gestionnaire, un port déjà pris ferait remonter une exception non
// interceptée qui ferait tomber tout le processus principal d'Electron — donc
// le jeu entier, à cause d'une fonctionnalité annexe.
bridgeServer.on("error", (err) => {
  console.warn("[panel-bridge] désactivé :", err && err.message);
});

bridgeServer.listen(BRIDGE_PORT, "127.0.0.1", () => {
  console.log(`[panel-bridge] 127.0.0.1:${BRIDGE_PORT}`);
});

const { registerHandler } = require("../utils/register-handler");
const { sanitizeApiAuthContext } = require("../api-scope");

function registerPanelHandlers(ctx) {
  const { apiScope } = ctx;

  registerHandler("api:getPanelData", async (event, payload) => {
    if (!payload || !payload.panelId)
      return {
        success: false,
        error: "BAD_REQUEST",
        message: "panelId is required",
      };
    const authContext = sanitizeApiAuthContext(payload && payload.authContext);
    const api = hasAuthContext(authContext)
      ? apiScope.resolveScopedApi(event, authContext)
      : apiScope.resolveScopedApi(event);
    scopedApi = api; // l'ouverture d'un panneau suffit à amorcer le pont
    const data = await api.get(
      `/api/panels/${encodeURIComponent(payload.panelId)}`,
    );
    // Journalise l'ouverture : c'est ce qui contient les recettes de fusion,
    // la liste des offres boutique, etc. selon le panneau ouvert.
    logPanel("OPEN", { panelId: payload.panelId, data });
    return data;
  });

  registerHandler("api:executePanelAction", async (event, payload) => {
    if (!payload || !payload.panelId)
      return {
        success: false,
        error: "BAD_REQUEST",
        message: "panelId is required",
      };
    const params =
      payload.params && typeof payload.params === "object"
        ? payload.params
        : {};
    const action = normalizeAction(payload.panelId, payload.action, params);
    if (!action)
      return {
        success: false,
        error: "BAD_REQUEST",
        message: "action is required",
      };
    const authContext = sanitizeApiAuthContext(payload && payload.authContext);
    const api = hasAuthContext(authContext)
      ? apiScope.resolveScopedApi(event, authContext)
      : apiScope.resolveScopedApi(event);
    scopedApi = api; // <- la ligne qui manquait : capture l'appelant authentifié
    const result = await api.post(
      `/api/panels/${encodeURIComponent(payload.panelId)}/action`,
      { action, params },
    );
    logPanel("REQ", { panelId: payload.panelId, action, params });

    // Group-start dispatch is now handled entirely via WebSocket push from the Java server.
    // The server publishes GroupStartEvent to all group members via GameEventBus
    // → GameEventWebSocketServer → ws-bridge.js → React useServerEvents().

    logPanel("RES", { panelId: payload.panelId, action, result });

    return result;
  });

  // Legacy polling handlers — kept as no-ops for backward compatibility
  // (older overlay builds may still call these)
  registerHandler("panel:startDjPolling", async () => {
    return {
      success: true,
      polling: false,
      message: "Polling replaced by WebSocket push",
    };
  });

  registerHandler("panel:stopDjPolling", async () => {
    return { success: true, polling: false };
  });

  registerHandler("panel:isDjPolling", async () => {
    return { success: true, polling: false };
  });
}

function hasAuthContext(authContext) {
  return !!(
    authContext &&
    typeof authContext === "object" &&
    (authContext.playerId > 0 || authContext.accountId > 0)
  );
}

function normalizeAction(panelId, action, params) {
  if (action) return String(action).trim();
  // Legacy inference
  if (panelId === "teleport" && params && params.destinationId != null)
    return "teleport";
  if (
    panelId === "shop" &&
    params &&
    (params.offerId != null || params.itemId != null)
  )
    return "purchase";
  return null;
}

module.exports = { registerPanelHandlers };
