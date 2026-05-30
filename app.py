import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import unquote, urlsplit

HOST = '0.0.0.0'
PORT = 9002
DATA_DIR = Path(os.environ.get('TOPOLOGY_DATA_DIR', '/app/data'))
TOPOLOGIES_DIR = DATA_DIR / 'topologies'
BASE_IMAGE = 'scl-plugin-network-topology-ubuntu:0.1'
LLM_URL = os.environ.get('DASHBOARD_LLM_URL', 'http://dashboard/api/llm/chat')
SERVER = None
JOBS = {}
JOBS_LOCK = threading.Lock()

HOST_TYPES = {
    'web-server': {
        'label': 'Web server',
        'ports': ['80/tcp'],
        'description': 'HTTP service with seeded web content.',
    },
    'db': {
        'label': 'Database',
        'ports': ['5432/tcp', '3306/tcp'],
        'description': 'Database-like host with SQLite seed data and DB service hints.',
    },
    'file-server': {
        'label': 'File server',
        'ports': ['8080/tcp'],
        'description': 'HTTP file share seeded with documents.',
    },
    'domain-admin': {
        'label': 'Domain admin',
        'ports': ['22/tcp'],
        'description': 'Privileged workstation identity for AD-style exercises.',
    },
    'normal-user': {
        'label': 'Normal user',
        'ports': ['22/tcp'],
        'description': 'Standard workstation user with low privileges.',
    },
    'jump-box': {
        'label': 'Jump box',
        'ports': ['22/tcp'],
        'description': 'Pivot host intended to bridge access paths.',
    },
    'log-server': {
        'label': 'Log server',
        'ports': ['514/tcp'],
        'description': 'Host prepared with log files for investigation.',
    },
}

INDEX_HTML = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Network Topology Builder</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f4f6f8;
        --panel: #ffffff;
        --line: #cfd8e3;
        --ink: #17202a;
        --muted: #5d6978;
        --accent: #1167b1;
        --danger: #a93226;
        --ok: #18794e;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--ink);
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      main {
        width: 100%;
        max-width: none;
        margin: 0 auto;
        padding: 18px 20px 22px;
      }
      header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 18px;
      }
      h1, h2, h3 { margin: 0; }
      h1 { font-size: 28px; }
      h2 { font-size: 18px; margin-bottom: 12px; }
      h3 { font-size: 15px; margin-bottom: 10px; }
      p { color: var(--muted); line-height: 1.45; }
      button, input, select, textarea {
        font: inherit;
      }
      button {
        border: 1px solid var(--accent);
        background: var(--accent);
        color: white;
        border-radius: 6px;
        padding: 8px 12px;
        cursor: pointer;
      }
      button.secondary {
        background: white;
        color: var(--accent);
      }
      button.danger {
        border-color: var(--danger);
        background: var(--danger);
      }
      button:disabled {
        opacity: .55;
        cursor: not-allowed;
      }
      input, select, textarea {
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 8px;
        background: white;
        color: var(--ink);
      }
      textarea {
        min-height: 90px;
        resize: vertical;
      }
      label {
        display: block;
        color: #344054;
        font-size: 13px;
        font-weight: 600;
        margin-bottom: 5px;
      }
      .grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(420px, 0.78fr);
        gap: 18px;
        align-items: start;
      }
      .panel {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 16px;
        min-width: 0;
      }
      .row {
        display: grid;
        grid-template-columns: repeat(12, minmax(0, 1fr));
        gap: 10px;
        align-items: end;
      }
      .span-2 { grid-column: span 2; }
      .span-3 { grid-column: span 3; }
      .span-4 { grid-column: span 4; }
      .span-5 { grid-column: span 5; }
      .span-6 { grid-column: span 6; }
      .span-8 { grid-column: span 8; }
      .span-12 { grid-column: span 12; }
      .network, .host, .saved-item {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 12px;
        background: #fbfcfe;
      }
      .host {
        background: white;
      }
      .network {
        background: #f8fbff;
      }
      .network-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        margin-bottom: 10px;
      }
      .network-body {
        border-top: 1px dashed #d9e2ee;
        margin-top: 12px;
        padding-top: 12px;
      }
      .host-list {
        display: grid;
        gap: 12px;
      }
      .toolbar {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        align-items: center;
      }
      .muted { color: var(--muted); }
      .status {
        min-height: 22px;
        color: var(--muted);
        margin-top: 10px;
        position: sticky;
        bottom: 8px;
        z-index: 2;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 8px 10px;
        background: white;
      }
      .pill {
        display: inline-flex;
        align-items: center;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 2px 8px;
        font-size: 12px;
        background: white;
        color: var(--muted);
      }
      .pill.ok { color: var(--ok); border-color: #9bd3b5; }
      .pill.bad { color: var(--danger); border-color: #e5aaa3; }
      .checkbox-line {
        display: flex;
        align-items: center;
        gap: 8px;
        font-weight: 500;
        color: #344054;
      }
      .checkbox-line input {
        width: auto;
      }
      .pair-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
        gap: 8px;
      }
      .saved-item h3 {
        display: flex;
        justify-content: space-between;
        gap: 8px;
      }
      pre {
        overflow: auto;
        min-height: 360px;
        max-height: 760px;
        background: #111827;
        color: #e5e7eb;
        padding: 12px;
        border-radius: 8px;
        font-size: 12px;
      }
      textarea.json {
        min-height: 360px;
        max-height: 760px;
        resize: vertical;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        white-space: pre;
      }
      .router-box {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #f8fbff;
        padding: 12px;
        margin-bottom: 12px;
      }
      @media (max-width: 920px) {
        .grid { grid-template-columns: 1fr; }
        .span-2, .span-3, .span-4, .span-5, .span-6, .span-8 { grid-column: span 12; }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <h1>Network Topology Builder</h1>
          <p>Create routed Ubuntu-based SCL labs with segmented networks, host roles, firewall rules, users, and generated data.</p>
        </div>
        <div class="toolbar">
          <button id="saveTopology">Save topology</button>
          <button class="secondary" id="newTopology">New</button>
        </div>
      </header>
      <div class="grid">
        <section class="panel">
          <h2>Topology</h2>
          <div class="row">
            <div class="span-5">
              <label for="topologyName">Name</label>
              <input id="topologyName" value="Corporate training lab">
            </div>
            <div class="span-3">
              <label for="networkCount">Networks</label>
              <input id="networkCount" type="number" min="1" max="8" value="3">
            </div>
            <div class="span-4">
              <label for="defaultHosts">Default hosts per network</label>
              <input id="defaultHosts" type="number" min="1" max="12" value="3">
            </div>
          </div>
          <p class="muted">Each topology is a single lab: all networks attach to the same router, and SSH is enabled per host when you need direct access.</p>
          <div class="router-box">
            <h3>Router</h3>
            <div class="row">
              <div class="span-3">
                <label class="checkbox-line" style="margin: 0">
                  <input id="routerSshEnabled" type="checkbox">
                  <span>Enable SSH on router</span>
                </label>
              </div>
              <div class="span-3">
                <label for="routerUsername">SSH user</label>
                <input id="routerUsername" value="admin">
              </div>
              <div class="span-3">
                <label for="routerPassword">SSH password</label>
                <input id="routerPassword" value="strato">
              </div>
              <div class="span-3">
                <label>&nbsp;</label>
                <div class="muted">Use these credentials to reach the router from `playground-net`.</div>
              </div>
            </div>
          </div>
          <div class="row" style="margin-bottom: 10px">
            <div class="span-6">
              <label for="hackerlabNetwork">Hackerlab network</label>
              <select id="hackerlabNetwork"></select>
            </div>
            <div class="span-6">
              <div class="muted" style="padding-top: 28px">
                The `scl-hackerlab` container is attached here so you can start from that network and reach its hosts.
              </div>
            </div>
          </div>
          <div class="toolbar">
            <button class="secondary" id="rebuildNetworks">Apply network count</button>
            <button class="secondary" id="balancedPreset">Balanced preset</button>
            <button class="secondary" id="enterprisePreset">Enterprise preset</button>
          </div>
          <div id="networks"></div>
          <h2>Firewall</h2>
          <p class="muted">Allowed paths are directional. Return traffic for established connections is automatically allowed.</p>
          <div id="firewallPairs" class="pair-grid"></div>
          <div class="status" id="status"></div>
        </section>
        <aside class="panel">
          <h2>Saved Topologies</h2>
          <div id="savedTopologies"></div>
          <h2>Selected Topology JSON</h2>
          <textarea id="selectedJson" class="json" readonly>{}</textarea>
        </aside>
      </div>
    </main>
    <script>
      const HOST_TYPES = __HOST_TYPES__;
      const networksEl = document.getElementById('networks');
      const pairsEl = document.getElementById('firewallPairs');
      const statusEl = document.getElementById('status');
      const savedEl = document.getElementById('savedTopologies');
      const selectedJsonEl = document.getElementById('selectedJson');
      const topologyName = document.getElementById('topologyName');
      const networkCount = document.getElementById('networkCount');
      const defaultHosts = document.getElementById('defaultHosts');
      const routerSshEnabled = document.getElementById('routerSshEnabled');
      const routerUsername = document.getElementById('routerUsername');
      const routerPassword = document.getElementById('routerPassword');
      const hackerlabNetwork = document.getElementById('hackerlabNetwork');

      let model = null;
      let saved = [];
      let selectedId = null;

      function roleOptions(selected) {
        return Object.entries(HOST_TYPES).map(([value, info]) => {
          return `<option value="${value}" ${value === selected ? 'selected' : ''}>${info.label}</option>`;
        }).join('');
      }

      function defaultNetworks(count = 3, hostsPerNetwork = 3) {
        const names = ['dmz', 'corp', 'admin', 'data', 'lab', 'guest', 'ops', 'dev'];
        return Array.from({ length: count }, (_, i) => {
          const roles = i === 0 ? ['web-server', 'file-server', 'normal-user']
            : i === 1 ? ['normal-user', 'db', 'log-server']
            : i === 2 ? ['domain-admin', 'jump-box', 'normal-user']
            : ['normal-user', 'web-server', 'db'];
          return {
            id: `net${i + 1}`,
            name: names[i] || `net${i + 1}`,
            cidr: `10.77.${i + 1}.0/24`,
            internet: i === 0,
            hosts: Array.from({ length: hostsPerNetwork }, (_, h) => ({
              id: `h${i + 1}_${h + 1}`,
              name: `${names[i] || `net${i + 1}`}-${h + 1}`,
              type: roles[h % roles.length],
              image: 'ubuntu:24.04',
              ssh_enabled: false,
              username: h === 0 && i === 2 ? 'admin' : 'student',
              password: h === 0 && i === 2 ? 'StratoAdmin!23' : 'strato',
              generate_data: ['web-server', 'file-server', 'db', 'log-server'].includes(roles[h % roles.length]),
              data_prompt: '',
              data_content: ''
            }))
          };
        });
      }

      function defaultFirewall(networks) {
        const allowed = [];
        for (let i = 0; i < networks.length; i++) {
          for (let j = 0; j < networks.length; j++) {
            if (i !== j && (i === 2 || j === 0)) {
              allowed.push(`${networks[i].id}->${networks[j].id}`);
            }
          }
        }
        return allowed;
      }

      function resetModel(count = 3, hosts = 3) {
        const nets = defaultNetworks(count, hosts);
        model = {
          id: selectedId,
          name: topologyName.value || 'Corporate training lab',
          created_at: null,
          updated_at: null,
          router: {
            ssh_enabled: false,
            username: 'admin',
            password: 'strato',
            firewall: { allowed: defaultFirewall(nets) }
          },
          infrastructure: { hackerlab_network_id: nets[0]?.id || '' },
          networks: nets
        };
        render();
      }

      function render() {
        if (!model) resetModel();
        model.router = model.router || {};
        model.router.ssh_enabled = Boolean(model.router.ssh_enabled);
        model.router.username = model.router.username || 'admin';
        model.router.password = model.router.password || 'strato';
        model.router.firewall = model.router.firewall || {};
        model.router.firewall.allowed = model.router.firewall.allowed || [];
        model.infrastructure = model.infrastructure || {};
        if (!model.infrastructure.hackerlab_network_id || !model.networks.find((network) => network.id === model.infrastructure.hackerlab_network_id)) {
          model.infrastructure.hackerlab_network_id = model.networks[0]?.id || '';
        }
        topologyName.value = model.name || '';
        networkCount.value = model.networks.length;
        routerSshEnabled.checked = model.router.ssh_enabled;
        routerUsername.value = model.router.username;
        routerPassword.value = model.router.password;
        hackerlabNetwork.innerHTML = model.networks.map((network) => `<option value="${escapeHtml(network.id)}" ${network.id === model.infrastructure.hackerlab_network_id ? 'selected' : ''}>${escapeHtml(network.name)}</option>`).join('');
        hackerlabNetwork.value = model.infrastructure.hackerlab_network_id || '';
        networksEl.innerHTML = model.networks.map((network, index) => networkTemplate(network, index)).join('');
        pairsEl.innerHTML = pairTemplate();
        selectedJsonEl.value = JSON.stringify(collect(), null, 2);
      }

      function networkTemplate(network, index) {
        return `
          <div class="network" data-network="${index}">
            <div class="network-head">
              <h3>${escapeHtml(network.name || `Network ${index + 1}`)}</h3>
              <div class="toolbar">
                <button class="secondary" data-action="delete-network" data-network-index="${index}">Delete network</button>
              </div>
            </div>
            <div class="row">
              <div class="span-3">
                <label>Name</label>
                <input data-field="network.name" value="${escapeHtml(network.name)}">
              </div>
              <div class="span-3">
                <label>CIDR</label>
                <input data-field="network.cidr" value="${escapeHtml(network.cidr)}">
              </div>
              <div class="span-3">
                <label>Hosts</label>
                <input data-field="network.hostCount" type="number" min="1" max="24" value="${network.hosts.length}">
              </div>
              <div class="span-3">
                <label>Internet</label>
                <div class="checkbox-line">
                  <input data-field="network.internet" type="checkbox" ${network.internet ? 'checked' : ''}>
                  <span>Allow egress</span>
                </div>
              </div>
            </div>
            <div class="toolbar" style="margin: 10px 0">
              <button class="secondary" data-action="apply-host-count" data-network-index="${index}">Apply hosts</button>
              <button class="secondary" data-action="add-host" data-network-index="${index}">Add host</button>
            </div>
            <div class="network-body">
              <div class="host-list">
                ${network.hosts.map((host, hostIndex) => hostTemplate(host, index, hostIndex)).join('')}
              </div>
            </div>
          </div>
        `;
      }

      function hostTemplate(host, networkIndex, hostIndex) {
        return `
          <div class="host" data-host="${hostIndex}">
            <div class="row">
              <div class="span-3">
                <label>Host name</label>
                <input data-field="host.name" value="${escapeHtml(host.name)}">
              </div>
              <div class="span-3">
                <label>Type</label>
                <select data-field="host.type">${roleOptions(host.type)}</select>
              </div>
              <div class="span-2">
                <label>SSH user</label>
                <input data-field="host.username" value="${escapeHtml(host.username || 'student')}">
              </div>
              <div class="span-2">
                <label>SSH password</label>
                <input data-field="host.password" value="${escapeHtml(host.password || 'strato')}">
              </div>
              <div class="span-2">
                <label>AI data</label>
                <div class="checkbox-line">
                  <input data-field="host.generate_data" type="checkbox" ${host.generate_data ? 'checked' : ''}>
                  <span>Use</span>
                </div>
              </div>
              <div class="span-12">
                <label class="checkbox-line" style="margin: 0">
                  <input data-field="host.ssh_enabled" type="checkbox" ${host.ssh_enabled ? 'checked' : ''}>
                  <span>Enable SSH on this host</span>
                </label>
              </div>
              <div class="span-8">
                <label>Data prompt</label>
                <input data-field="host.data_prompt" placeholder="Example: internal invoices for a fake finance department" value="${escapeHtml(host.data_prompt || '')}">
              </div>
              <div class="span-4 toolbar">
                <button class="secondary" data-action="generate-host-data" data-network-index="${networkIndex}" data-host-index="${hostIndex}">Generate data</button>
                <button class="danger" data-action="remove-host" data-network-index="${networkIndex}" data-host-index="${hostIndex}">Remove</button>
              </div>
              <div class="span-12">
                <label>Data content</label>
                <textarea data-field="host.data_content">${escapeHtml(host.data_content || '')}</textarea>
              </div>
            </div>
          </div>
        `;
      }

      function pairTemplate() {
        const allowed = new Set(model.router.firewall.allowed || []);
        const parts = [];
        for (const from of model.networks) {
          for (const to of model.networks) {
            if (from.id === to.id) continue;
            const key = `${from.id}->${to.id}`;
            parts.push(`
              <label class="checkbox-line">
                <input type="checkbox" data-firewall="${key}" ${allowed.has(key) ? 'checked' : ''}>
                <span>${escapeHtml(from.name)} -> ${escapeHtml(to.name)}</span>
              </label>
            `);
          }
        }
        return parts.join('');
      }

      function collect() {
        if (!model) resetModel();
        model.name = topologyName.value.trim() || 'Untitled topology';
        model.router = model.router || {};
        model.router.ssh_enabled = Boolean(routerSshEnabled.checked);
        model.router.username = routerUsername.value.trim() || 'admin';
        model.router.password = routerPassword.value || 'strato';
        model.infrastructure = model.infrastructure || {};
        model.infrastructure.hackerlab_network_id = hackerlabNetwork.value || model.networks[0]?.id || '';
        document.querySelectorAll('[data-network]').forEach((networkEl) => {
          const network = model.networks[Number(networkEl.dataset.network)];
          network.name = valueOf(networkEl, 'network.name') || network.name;
          network.cidr = valueOf(networkEl, 'network.cidr') || network.cidr;
          network.internet = checkedOf(networkEl, 'network.internet');
          network.hosts = network.hosts.map((host, hostIndex) => {
            const hostEl = networkEl.querySelector(`[data-host="${hostIndex}"]`);
            if (!hostEl) return host;
              return {
                ...host,
                name: valueOf(hostEl, 'host.name') || host.name,
                type: valueOf(hostEl, 'host.type') || host.type,
                username: valueOf(hostEl, 'host.username') || 'student',
                password: valueOf(hostEl, 'host.password') || 'strato',
                ssh_enabled: checkedOf(hostEl, 'host.ssh_enabled'),
                generate_data: checkedOf(hostEl, 'host.generate_data'),
                data_prompt: valueOf(hostEl, 'host.data_prompt'),
                data_content: valueOf(hostEl, 'host.data_content')
              };
            });
        });
        model.router.firewall = model.router.firewall || {};
        model.router.firewall.allowed = Array.from(document.querySelectorAll('[data-firewall]:checked')).map((el) => el.dataset.firewall);
        selectedJsonEl.value = JSON.stringify(model, null, 2);
        return model;
      }

      function valueOf(root, name) {
        const el = root.querySelector(`[data-field="${name}"]`);
        return el ? el.value.trim() : '';
      }

      function checkedOf(root, name) {
        const el = root.querySelector(`[data-field="${name}"]`);
        return Boolean(el && el.checked);
      }

      function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, (ch) => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
        })[ch]);
      }

      function setStatus(message) {
        statusEl.textContent = message || '';
      }

      async function api(path, options = {}) {
        const res = await fetch(path, {
          ...options,
          headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
        });
        const text = await res.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
        if (!res.ok) throw new Error(data.error || text || `Request failed with ${res.status}`);
        return data;
      }

      async function waitForJob(jobId, progressMessage) {
        while (true) {
          const job = (await api(`api/jobs/${encodeURIComponent(jobId)}`)).job;
          if (job.status === 'completed') return job.result || {};
          if (job.status === 'failed') throw new Error(job.error || 'Background job failed');
          setStatus(progressMessage);
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }

      async function refreshSaved() {
        saved = (await api('api/topologies')).topologies || [];
        savedEl.innerHTML = saved.length ? saved.map((item) => `
          <div class="saved-item">
            <h3>
              <span>${escapeHtml(item.name)}</span>
              <span class="pill ${item.running ? 'ok' : ''}">${item.running ? 'running' : 'stopped'}</span>
            </h3>
            <p>${item.networks} network(s), ${item.hosts} host(s)</p>
            <div class="toolbar">
              <button class="secondary" data-action="load-topology" data-id="${item.id}">Load</button>
              <button data-action="start-topology" data-id="${item.id}">Start</button>
              <button class="secondary" data-action="stop-topology" data-id="${item.id}">Stop</button>
            </div>
          </div>
        `).join('') : '<p class="muted">No saved topologies yet.</p>';
      }

      async function saveTopology() {
        setStatus('Saving topology...');
        const payload = collect();
        const result = await api('api/topologies', { method: 'POST', body: JSON.stringify(payload) });
        selectedId = result.topology.id;
        model = result.topology;
        setStatus('Topology saved.');
        render();
        await refreshSaved();
      }

      async function loadTopology(id) {
        const result = await api(`api/topologies/${encodeURIComponent(id)}`);
        selectedId = result.topology.id;
        model = result.topology;
        setStatus(`Loaded ${model.name}.`);
        render();
      }

      async function startTopology(id) {
        setStatus('Starting topology. The first run may build the Ubuntu base image.');
        const job = await api(`api/topologies/${encodeURIComponent(id)}/start`, { method: 'POST', body: '{}' });
        await waitForJob(job.job_id, 'Starting topology. The first run may build the Ubuntu base image.');
        setStatus('Topology started.');
        await refreshSaved();
      }

      async function stopTopology(id) {
        setStatus('Stopping topology...');
        const job = await api(`api/topologies/${encodeURIComponent(id)}/stop`, { method: 'POST', body: '{}' });
        await waitForJob(job.job_id, 'Stopping topology...');
        setStatus('Topology stopped.');
        await refreshSaved();
      }

      async function generateHostData(networkIndex, hostIndex) {
        collect();
        const host = model.networks[networkIndex].hosts[hostIndex];
        setStatus(`Generating data for ${host.name}...`);
        try {
          const job = await api('api/generate-data', {
            method: 'POST',
            body: JSON.stringify({ host, topology: model })
          });
          const result = await waitForJob(job.job_id, `Generating data for ${host.name}...`);
          host.data_content = result.content || '';
          host.generate_data = true;
          setStatus(`Generated data for ${host.name}.`);
          render();
        } catch (error) {
          const message = `Could not generate data for ${host.name}: ${error.message}`;
          setStatus(message);
          alert(message);
        }
      }

      document.addEventListener('input', (event) => {
        if (event.target.matches('input, select, textarea')) collect();
      });

      document.addEventListener('click', async (event) => {
        const action = event.target.dataset.action;
        try {
          if (event.target.id === 'saveTopology') return saveTopology();
          if (event.target.id === 'newTopology') {
            selectedId = null;
            resetModel(Number(networkCount.value) || 3, Number(defaultHosts.value) || 3);
            return;
          }
          if (event.target.id === 'rebuildNetworks') {
            selectedId = null;
            resetModel(Number(networkCount.value) || 3, Number(defaultHosts.value) || 3);
            return;
          }
          if (event.target.id === 'balancedPreset') {
            topologyName.value = 'Balanced three-zone lab';
            selectedId = null;
            resetModel(3, 3);
            return;
          }
          if (event.target.id === 'enterprisePreset') {
            topologyName.value = 'Enterprise segmented lab';
            selectedId = null;
            resetModel(5, 4);
            return;
          }
          if (event.target.id === 'hackerlabNetwork') {
            collect();
            render();
            return;
          }
          if (action === 'apply-host-count') {
            collect();
            const i = Number(event.target.dataset.networkIndex);
            const networkEl = document.querySelector(`[data-network="${i}"]`);
            const count = Math.max(1, Math.min(24, Number(valueOf(networkEl, 'network.hostCount')) || 1));
            const current = model.networks[i].hosts;
            while (current.length < count) {
              current.push({ id: `h${i + 1}_${current.length + 1}`, name: `${model.networks[i].name}-${current.length + 1}`, type: 'normal-user', image: 'ubuntu:24.04', ssh_enabled: false, username: 'student', password: 'strato', generate_data: false, data_prompt: '', data_content: '' });
            }
            current.length = count;
            render();
          }
          if (action === 'add-host') {
            collect();
            const i = Number(event.target.dataset.networkIndex);
            const current = model.networks[i].hosts;
            current.push({ id: `h${i + 1}_${current.length + 1}`, name: `${model.networks[i].name}-${current.length + 1}`, type: 'normal-user', image: 'ubuntu:24.04', ssh_enabled: false, username: 'student', password: 'strato', generate_data: false, data_prompt: '', data_content: '' });
            render();
          }
          if (action === 'delete-network') {
            collect();
            if (model.networks.length <= 1) {
              setStatus('At least one network is required.');
              return;
            }
            model.networks.splice(Number(event.target.dataset.networkIndex), 1);
            if (model.infrastructure?.hackerlab_network_id && !model.networks.find((network) => network.id === model.infrastructure.hackerlab_network_id)) {
              model.infrastructure.hackerlab_network_id = model.networks[0]?.id || '';
            }
            const validPairs = new Set(model.networks.flatMap((from) => model.networks.filter((to) => to.id !== from.id).map((to) => `${from.id}->${to.id}`)));
            model.router.firewall.allowed = (model.router.firewall.allowed || []).filter((pair) => validPairs.has(pair));
            render();
          }
          if (action === 'remove-host') {
            collect();
            model.networks[Number(event.target.dataset.networkIndex)].hosts.splice(Number(event.target.dataset.hostIndex), 1);
            render();
          }
          if (action === 'generate-host-data') return generateHostData(Number(event.target.dataset.networkIndex), Number(event.target.dataset.hostIndex));
          if (action === 'load-topology') return loadTopology(event.target.dataset.id);
          if (action === 'start-topology') return startTopology(event.target.dataset.id);
          if (action === 'stop-topology') return stopTopology(event.target.dataset.id);
        } catch (error) {
          setStatus(`Error: ${error.message}`);
        }
      });

      resetModel();
      refreshSaved().catch((error) => setStatus(`Error: ${error.message}`));
    </script>
  </body>
</html>
"""


def now_ts():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def start_job(task):
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {'id': job_id, 'status': 'running', 'result': None, 'error': ''}

    def worker():
        try:
            result = task()
        except Exception as exc:  # pragma: no cover - depends on Docker and Ollama runtime
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'failed'
                JOBS[job_id]['error'] = str(exc)
        else:
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'completed'
                JOBS[job_id]['result'] = result or {}

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id) or {})


def slugify(value):
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug or f'topology-{uuid.uuid4().hex[:8]}'


def topology_dir(topology_id):
    return TOPOLOGIES_DIR / topology_id


def topology_path(topology_id):
    return topology_dir(topology_id) / 'topology.json'


def compose_path(topology_id):
    return topology_dir(topology_id) / 'docker-compose.yml'


def read_json(path):
    with open(path, 'r', encoding='utf8') as file:
        return json.load(file)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf8') as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write('\n')


def normalize_identifier(value, fallback):
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(value or '')).strip('-').lower()
    return normalized or fallback


def validate_topology(topology):
    if not isinstance(topology, dict):
        raise ValueError('Topology must be a JSON object.')
    name = str(topology.get('name') or '').strip()
    if not name:
        raise ValueError('Topology name is required.')
    networks = topology.get('networks')
    if not isinstance(networks, list) or not networks:
        raise ValueError('At least one network is required.')
    if len(networks) > 8:
        raise ValueError('At most 8 networks are supported in this first version.')

    seen_networks = set()
    for index, network in enumerate(networks, start=1):
        network['id'] = normalize_identifier(network.get('id'), f'net{index}')
        network['name'] = str(network.get('name') or network['id']).strip()
        network['cidr'] = str(network.get('cidr') or f'10.77.{index}.0/24').strip()
        network['internet'] = bool(network.get('internet'))
        if network['id'] in seen_networks:
            raise ValueError(f"Duplicate network id '{network['id']}'.")
        seen_networks.add(network['id'])
        hosts = network.get('hosts')
        if not isinstance(hosts, list) or not hosts:
            raise ValueError(f"Network '{network['name']}' needs at least one host.")
        if len(hosts) > 24:
            raise ValueError(f"Network '{network['name']}' has more than 24 hosts.")
        for host_index, host in enumerate(hosts, start=1):
            host['id'] = normalize_identifier(host.get('id'), f'h{index}_{host_index}')
            host['name'] = normalize_identifier(host.get('name'), f'{network["id"]}-{host_index}')
            host['type'] = host.get('type') if host.get('type') in HOST_TYPES else 'normal-user'
            host['image'] = 'ubuntu:24.04'
            host['username'] = normalize_identifier(host.get('username'), 'student')
            host['password'] = str(host.get('password') or 'strato')
            host['generate_data'] = bool(host.get('generate_data'))
            host['data_prompt'] = str(host.get('data_prompt') or '')
            host['data_content'] = str(host.get('data_content') or '')

    firewall = topology.setdefault('router', {}).setdefault('firewall', {})
    allowed = firewall.get('allowed') or []
    firewall['allowed'] = [
        pair for pair in allowed
        if isinstance(pair, str) and '->' in pair
    ]
    router = topology.setdefault('router', {})
    router['ssh_enabled'] = bool(router.get('ssh_enabled'))
    router['username'] = normalize_identifier(router.get('username'), 'admin')
    router['password'] = str(router.get('password') or 'strato')
    infrastructure = topology.setdefault('infrastructure', {})
    hackerlab_network_id = infrastructure.get('hackerlab_network_id')
    if not hackerlab_network_id or hackerlab_network_id not in seen_networks:
        infrastructure['hackerlab_network_id'] = networks[0]['id']
    return topology


def summarize(topology):
    return {
        'id': topology['id'],
        'name': topology['name'],
        'created_at': topology.get('created_at'),
        'updated_at': topology.get('updated_at'),
        'networks': len(topology.get('networks', [])),
        'hosts': sum(len(network.get('hosts', [])) for network in topology.get('networks', [])),
        'running': is_running(topology['id']),
    }


def list_topologies():
    TOPOLOGIES_DIR.mkdir(parents=True, exist_ok=True)
    topologies = []
    for path in sorted(TOPOLOGIES_DIR.glob('*/topology.json')):
        try:
            topology = read_json(path)
            if str(topology.get('name') or '').strip().lower() == 'ssh lab':
                try:
                    path.unlink()
                except OSError:
                    continue
                compose_file = compose_path(path.parent.name)
                if compose_file.exists():
                    try:
                        compose_file.unlink()
                    except OSError:
                        pass
                continue
            topologies.append(summarize(topology))
        except (OSError, json.JSONDecodeError):
            continue
    return topologies


def subnet_prefix(cidr):
    return cidr.split('/')[0].rsplit('.', 1)[0]


def router_ip(cidr):
    return f'{subnet_prefix(cidr)}.254'


def host_ip(cidr, host_index):
    return f'{subnet_prefix(cidr)}.{10 + host_index}'


def hackerlab_ip(cidr):
    return f'{subnet_prefix(cidr)}.2'


def shell_quote(value):
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def ssh_setup_block(username, password):
    return f"""mkdir -p /var/run/sshd
useradd -m -s /bin/bash {shell_quote(username)} 2>/dev/null || true
echo {shell_quote(username + ':' + password)} | chpasswd || true
/usr/sbin/sshd || true
"""


def host_script(topology, network, host, host_index):
    role = HOST_TYPES[host['type']]['label']
    ip_addr = host_ip(network['cidr'], host_index)
    gateway = router_ip(network['cidr'])
    data_content = host.get('data_content') or default_data_for_host(topology, network, host)
    service_block = role_service_block(host['type'])
    ssh_block = ''
    if host.get('ssh_enabled'):
        ssh_block = ssh_setup_block(host['username'], host['password'])
    return f"""set -eu
ip route replace default via {gateway} || true
mkdir -p /srv/scl-data /srv/www /srv/files /srv/db /var/log/scl
cat > /etc/scl-host.json <<'JSON'
{json.dumps({'topology': topology['name'], 'network': network['name'], 'host': host['name'], 'role': role, 'ip': ip_addr}, indent=2)}
JSON
cat > /srv/scl-data/README.txt <<'DATA'
{data_content}
DATA
cp /srv/scl-data/README.txt /srv/www/index.txt || true
cp /srv/scl-data/README.txt /srv/files/share.txt || true
{service_block}
{ssh_block}
tail -f /dev/null
"""


def router_management_block(router):
    if not router.get('ssh_enabled'):
        return ''
    return ssh_setup_block(router.get('username') or 'admin', router.get('password') or 'strato')


def hackerlab_script(network):
    gateway = router_ip(network['cidr'])
    return f"""set -eu
ip route replace default via {gateway} || true
exec /root/.start-container.sh
"""


def default_data_for_host(topology, network, host):
    return (
        f"Topology: {topology['name']}\n"
        f"Network: {network['name']}\n"
        f"Host: {host['name']}\n"
        f"Role: {HOST_TYPES[host['type']]['label']}\n"
        "No AI-generated data was requested for this host.\n"
    )


def role_service_block(host_type):
    if host_type == 'web-server':
        return "printf '<h1>SCL web server</h1><pre>%s</pre>' \"$(cat /srv/scl-data/README.txt)\" > /srv/www/index.html\npython3 -m http.server 80 -d /srv/www &"
    if host_type == 'file-server':
        return "python3 -m http.server 8080 -d /srv/files &"
    if host_type == 'db':
        return "sqlite3 /srv/db/app.db 'create table if not exists notes(id integer primary key, body text); insert into notes(body) values (readfile(\"/srv/scl-data/README.txt\"));' || true"
    if host_type == 'log-server':
        return "cp /srv/scl-data/README.txt /var/log/scl/training.log || true"
    return ":"


def router_script(topology):
    networks = topology['networks']
    router = topology.get('router', {})
    allowed_pairs = set(topology.get('router', {}).get('firewall', {}).get('allowed', []))
    forward_rules = []
    for network in networks:
        if network.get('internet'):
            forward_rules.append(f"ip saddr {network['cidr']} oifname \"$$wan_if\" accept")
    for source in networks:
        for dest in networks:
            if source['id'] == dest['id']:
                continue
            if f"{source['id']}->{dest['id']}" in allowed_pairs:
                forward_rules.append(f"ip saddr {source['cidr']} ip daddr {dest['cidr']} accept")
    if not forward_rules:
        forward_rules.append('counter drop')
    forward_block = '\n    '.join(forward_rules)
    return f"""set -eu
sysctl -w net.ipv4.ip_forward=1 || true
wan_if="$(ip route show default | awk '{{print $5; exit}}')"
cat > /tmp/router-rules.nft <<EOF
flush ruleset
table inet filter {{
  chain forward {{
    type filter hook forward priority 0; policy drop;
    ct state established,related accept
    iifname "lo" accept
    {forward_block}
  }}
}}
table ip nat {{
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    oifname "$$wan_if" masquerade
  }}
}}
EOF
nft -f /tmp/router-rules.nft || true
{router_management_block(router)}
    tail -f /dev/null
"""


def generate_compose(topology):
    project_prefix = f"SCL-topology-{topology['id']}"
    hackerlab_network_id = topology.get('infrastructure', {}).get('hackerlab_network_id')
    compose = {
        'services': {
            'router': {
                'image': BASE_IMAGE,
                'container_name': f'{project_prefix}-router',
                'hostname': 'router',
                'cap_add': ['NET_ADMIN'],
                'sysctls': {'net.ipv4.ip_forward': '1'},
                'command': ['sh', '-lc', router_script(topology)],
                'networks': {'playground-net': {}},
                'labels': ['scl.plugin=network-topology', f'scl.topology={topology["id"]}'],
            }
        },
        'networks': {
            'playground-net': {'external': True, 'name': 'playground-net'}
        }
    }
    for index, network in enumerate(topology['networks'], start=1):
        network_key = f'topo_{network["id"]}'
        compose['networks'][network_key] = {
            'name': f'{project_prefix}-{network["id"]}',
            'internal': True,
            'ipam': {'config': [{'subnet': network['cidr']}]},
        }
        compose['services']['router']['networks'][network_key] = {'ipv4_address': router_ip(network['cidr'])}
        for host_index, host in enumerate(network['hosts'], start=1):
            service_name = f'{network["id"]}-{host["id"]}'
            compose['services'][service_name] = {
                'image': BASE_IMAGE,
                'container_name': f'{project_prefix}-{service_name}',
                'hostname': host['name'],
                'cap_add': ['NET_ADMIN'],
                'command': ['sh', '-lc', host_script(topology, network, host, host_index)],
                'networks': {network_key: {'ipv4_address': host_ip(network['cidr'], host_index)}},
                'labels': [
                    'scl.plugin=network-topology',
                    f'scl.topology={topology["id"]}',
                    f'scl.network={network["id"]}',
                    f'scl.host_type={host["type"]}',
                ],
            }
        if network['id'] == hackerlab_network_id:
            compose['services']['hackerlab'] = {
                'image': 'scl-hackerlab',
                'container_name': f'{project_prefix}-hackerlab',
                'hostname': 'hackerlab',
                'cap_add': ['NET_ADMIN'],
                'command': ['sh', '-lc', hackerlab_script(network)],
                'networks': {network_key: {'ipv4_address': hackerlab_ip(network['cidr'])}},
                'labels': [
                    'scl.plugin=network-topology',
                    f'scl.topology={topology["id"]}',
                    f'scl.network={network["id"]}',
                    'scl.host_type=hackerlab',
                ],
            }
    return compose


def save_topology(payload):
    topology = validate_topology(payload)
    topology_id = normalize_identifier(topology.get('id'), slugify(topology['name']))
    if not topology.get('id'):
        topology_id = f'{topology_id}-{uuid.uuid4().hex[:6]}'
    topology['id'] = topology_id
    existing_path = topology_path(topology_id)
    existing = read_json(existing_path) if existing_path.exists() else {}
    topology['created_at'] = existing.get('created_at') or now_ts()
    topology['updated_at'] = now_ts()
    write_json(existing_path, topology)
    compose = generate_compose(topology)
    with open(compose_path(topology_id), 'w', encoding='utf8') as file:
        json.dump(compose, file, indent=2)
        file.write('\n')
    return topology


def docker_command():
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True, text=True).returncode == 0:
        return ['docker', 'compose']
    return ['docker-compose']


def ensure_base_image():
    result = subprocess.run(['docker', 'image', 'inspect', BASE_IMAGE], capture_output=True, text=True)
    if result.returncode == 0:
        return
    dockerfile = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl iproute2 iputils-ping netcat-openbsd nftables openssh-server python3 sqlite3 sudo \
  && mkdir -p /run/sshd \
  && rm -rf /var/lib/apt/lists/*
"""
    build = subprocess.run(
        ['docker', 'build', '-t', BASE_IMAGE, '-'],
        input=dockerfile,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or 'Docker image build failed')


def compose_project_name(topology_id):
    return f'SCL-topology-{topology_id}'


def run_compose(topology_id, args):
    file = compose_path(topology_id)
    if not file.exists():
        raise FileNotFoundError('Generated docker-compose.yml not found. Save the topology first.')
    cmd = docker_command() + ['-p', compose_project_name(topology_id), '-f', str(file)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Compose command failed: {cmd}')
    return result.stdout


def start_topology(topology_id):
    ensure_base_image()
    run_compose(topology_id, ['up', '-d'])
    return {'status': 'started'}


def stop_topology(topology_id):
    run_compose(topology_id, ['down'])
    return {'status': 'stopped'}


def is_running(topology_id):
    file = compose_path(topology_id)
    if not file.exists():
        return False
    try:
        output = run_compose(topology_id, ['ps', '--services', '--filter', 'status=running'])
    except Exception:
        return False
    return bool(output.strip())


def generate_data_with_llm(topology, host):
    prompt = host.get('data_prompt') or (
        f"Generate realistic but fictional training data for a {HOST_TYPES[host['type']]['label']} host "
        f"inside a cyber range topology named {topology.get('name', 'training lab')}."
    )
    message = {
        'role': 'user',
        'content': (
            "/no_think\n"
            "Create concise, realistic, fictional data for a local cyber range host. "
            "Do not include real secrets, real people, or harmful instructions. "
            "Return plain text only.\n\n"
            f"Host: {host.get('name')}\n"
            f"Role: {HOST_TYPES[host['type']]['label']}\n"
            f"Requested data: {prompt}"
        )
    }
    encoded = json.dumps([message]).encode('utf8')
    request = urllib_request.Request(
        LLM_URL,
        data=encoded,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib_request.urlopen(request, timeout=120) as response:
            messages = json.loads(response.read().decode('utf8'))
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode('utf8', errors='replace').strip()
        if 'All connection attempts failed' in detail:
            detail += '. Start the SCL Ollama service with: docker compose up -d ollama'
        raise RuntimeError(detail or f'SCL LLM request failed with HTTP {exc.code}') from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f'Could not reach the SCL dashboard LLM endpoint: {exc.reason}') from exc
    if isinstance(messages, list) and messages:
        content = str(messages[-1].get('content') or '')
        return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    return ''


class TopologyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.handle_get()
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def do_POST(self):
        try:
            self.handle_post()
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def handle_get(self):
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/':
            html = INDEX_HTML.replace('__HOST_TYPES__', json.dumps(HOST_TYPES))
            self.send_html(html)
            return
        if path == '/api/topologies':
            self.send_json(200, {'topologies': list_topologies()})
            return
        match = re.fullmatch(r'/api/jobs/([^/]+)', path)
        if match:
            job = get_job(unquote(match.group(1)))
            if not job:
                self.send_json(404, {'error': 'Job not found'})
                return
            self.send_json(200, {'job': job})
            return
        match = re.fullmatch(r'/api/topologies/([^/]+)', path)
        if match:
            topology_id = unquote(match.group(1))
            path_obj = topology_path(topology_id)
            if not path_obj.exists():
                self.send_json(404, {'error': 'Topology not found'})
                return
            self.send_json(200, {'topology': read_json(path_obj), 'running': is_running(topology_id)})
            return
        self.send_json(404, {'error': 'Not found'})

    def handle_post(self):
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/api/topologies':
            topology = save_topology(self.read_body())
            self.send_json(200, {'topology': topology})
            return
        if path == '/api/generate-data':
            body = self.read_body()
            job_id = start_job(lambda: {
                'content': generate_data_with_llm(body.get('topology') or {}, body.get('host') or {})
            })
            self.send_json(202, {'job_id': job_id})
            return
        match = re.fullmatch(r'/api/topologies/([^/]+)/(start|stop)', path)
        if match:
            topology_id = unquote(match.group(1))
            action = match.group(2)
            if action == 'start':
                job_id = start_job(lambda: start_topology(topology_id))
            else:
                job_id = start_job(lambda: stop_topology(topology_id))
            self.send_json(202, {'job_id': job_id})
            return
        self.send_json(404, {'error': 'Not found'})

    def read_body(self):
        length = int(self.headers.get('Content-Length') or '0')
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf8'))

    def log_message(self, format_string, *args):
        print(format_string % args)

    def send_html(self, body):
        encoded = body.encode('utf8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, status_code, payload):
        encoded = json.dumps(payload).encode('utf8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def handle_shutdown(signum, _frame):
    print(f'Received signal {signum}, shutting down network topology plugin.')
    if SERVER is not None:
        threading.Thread(target=SERVER.shutdown, daemon=True).start()


if __name__ == '__main__':
    TOPOLOGIES_DIR.mkdir(parents=True, exist_ok=True)
    SERVER = ThreadingHTTPServer((HOST, PORT), TopologyHandler)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    print(f'Network topology plugin listening on http://{HOST}:{PORT}')
    try:
        SERVER.serve_forever()
    finally:
        SERVER.server_close()
        print('Network topology plugin stopped.')
