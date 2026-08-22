// --- FRONTEND API INTEGRATION ---
const API_BASE = '/api';

// Persistent Local UI States
let activeCustomerId = null;
let activeWorkspaceQueueTab = 'active';
let activeAppModal = null;
let isClockedIn = false;

// Dynamic Data States
let mockCustomers = [];
let campaignConfigs = {};
let agents = [];
let globalStats = { totalCalls: 0, connected: 0, recovered: 0, outcomes: {} };

// --- RBAC: SECURITY & ENFORCEMENT ---
const CURRENT_USER_EMAIL = localStorage.getItem('USER_EMAIL');
const CURRENT_USER_ROLE = localStorage.getItem('USER_ROLE');
const CURRENT_USER_NAME = localStorage.getItem('LOGGED_IN_AGENT');
let LOGGED_IN_AGENT = CURRENT_USER_NAME || null; 
const currentPage = document.body.dataset.page;

function enforceSecurity() {
    // 1. Unauthenticated users get booted to login
    if (!CURRENT_USER_EMAIL && currentPage !== 'login') {
        window.location.replace('/login.html');
        return false;
    }
    
    // 2. Logged-in users on the login page get routed to their specific home pages
    if (CURRENT_USER_EMAIL && currentPage === 'login') {
        const dest = CURRENT_USER_ROLE === 'Agent' ? '/workspace.html' : '/teamleader.html';
        window.location.replace(dest);
        return false;
    }

    // 3. Restrict Agents to Workspace only
    if (CURRENT_USER_ROLE === 'Agent' && currentPage !== 'workspace' && currentPage !== 'login') {
        window.location.replace('/workspace.html');
        return false;
    }

    // 4. Restrict Team Leaders from the Workspace
    if (CURRENT_USER_ROLE === 'Team Leader' && currentPage === 'workspace') {
        window.location.replace('/teamleader.html');
        return false;
    }

    // 5. Update UI elements based on role
    document.addEventListener("DOMContentLoaded", () => {
        // Hide sidebar links
        if (CURRENT_USER_ROLE === 'Agent') {
            const restricted = document.querySelectorAll('a[data-page="campaigns"], a[data-page="teamleader"], a[data-page="dashboard"], a[data-page="admin"]');
            restricted.forEach(link => link.style.display = 'none');
        } else if (CURRENT_USER_ROLE === 'Team Leader') {
            const restricted = document.querySelectorAll('a[data-page="workspace"]');
            restricted.forEach(link => link.style.display = 'none');
        }

        // Convert the top-right area into a Logout Button for ALL users
        const agentSelector = document.getElementById('current-agent-select');
        if (agentSelector) {
            agentSelector.parentElement.innerHTML = `
                <button onclick="promptLogout()" class="font-bold text-lg text-brandDark hover:text-brandAmber transition flex items-center gap-2 cursor-pointer">
                    ${CURRENT_USER_NAME} <i class="fa-solid fa-right-from-bracket text-sm"></i>
                </button>
                <div class="text-[11px] text-brandAmber font-bold uppercase mt-1 pl-1">
                    ${CURRENT_USER_ROLE}
                </div>
            `;
        }
    });
    
    return true;
}

const isAuthorized = enforceSecurity();

window.onload = async () => {
    // Stop execution if they are unauthorized or on the login page
    if (!isAuthorized || currentPage === 'login') return; 
    
    await fetchAllData();
    if (!Array.isArray(agents)) agents = [];
    if (!Array.isArray(mockCustomers)) mockCustomers = [];
    
    initCurrentPage();
};

window.promptLogout = function() {
    let modal = document.getElementById('logout-modal-backdrop');
    
    // Inject the modal into the HTML if it doesn't exist yet
    if (!modal) {
        document.body.insertAdjacentHTML('beforeend', `
            <div id="logout-modal-backdrop" class="fixed inset-0 bg-brandDark/40 backdrop-blur-sm z-[100] flex items-center justify-center">
                <div class="glass-panel bg-white/90 p-6 rounded-2xl shadow-2xl max-w-sm w-full text-center border border-brandDark/10">
                    <i class="fa-solid fa-right-from-bracket text-4xl text-brandAmber mb-4"></i>
                    <h2 class="text-xl font-bold text-brandDark mb-2">Confirm Logout</h2>
                    <p class="text-brandDark/70 text-sm mb-6">Are you sure you want to log out of your session?</p>
                    <div class="flex justify-center gap-3">
                        <button onclick="closeLogoutModal()" class="px-5 py-2 rounded-lg text-sm font-bold text-brandDark/70 hover:bg-brandDark/10 transition border border-transparent">Cancel</button>
                        <button onclick="logout()" class="px-5 py-2 rounded-lg text-sm font-bold bg-brandAmber hover:bg-amber-600 text-white shadow-md transition">Yes, Log Out</button>
                    </div>
                </div>
            </div>
        `);
    } else {
        modal.classList.remove('hidden');
    }
};

window.closeLogoutModal = function() {
    const modal = document.getElementById('logout-modal-backdrop');
    if (modal) modal.classList.add('hidden');
};

window.logout = function() {
    localStorage.clear();
    window.location.replace('/login.html');
};
// ----------------------------------------------------------------------
// RENDER FUNCTIONS (Restored to fix Shift Manager and Campaigns)
// ----------------------------------------------------------------------

window.updateCampaignDropdowns = function() {
    const allocSelect = document.getElementById('alloc-campaign');
    if (allocSelect) {
        const camps = Object.keys(campaignConfigs);
        allocSelect.innerHTML = '<option value="">Select a campaign to allocate...</option>' + 
            camps.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
    }
};

window.renderShiftManager = function() {
    // Refresh the dropdown when the Shift Manager opens
    if (typeof updateCampaignDropdowns === 'function') {
        updateCampaignDropdowns();
    }
};

window.renderCampaignList = function() {
    const listDiv = document.getElementById('campaign-list-container');
    if (!listDiv) return;
    
    const camps = Object.keys(campaignConfigs);
    if (camps.length === 0) {
        listDiv.innerHTML = '<p class="text-brandDark/50 italic p-4">No campaigns found. Create one to get started.</p>';
        return;
    }
    
    // Renders the list of campaigns dynamically
    listDiv.innerHTML = camps.map(c => `
        <div class="glass-card p-4 rounded-xl mb-3 border border-brandDark/10 hover:border-brandAmber/50 transition">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="font-bold text-lg text-brandDark">${escapeHtml(c)}</h3>
                    <span class="text-xs font-bold text-brandDark/50 uppercase">${escapeHtml(campaignConfigs[c])}</span>
                </div>
                <i class="fa-solid fa-bullhorn text-brandAmber text-xl opacity-50"></i>
            </div>
        </div>
    `).join('');
};

window.renderTeamLeaderWorkspace = function() {
    // Optional: Add specific Team Leader UI updates here if needed
    console.log("Team Leader UI updated.");
};

window.renderCampaignAgentSelector = function() {
    // Optional: Add specific Agent Selector UI updates here if needed
};

// ----------------------------------------------------------------------
// DATA FETCHING & SYNCHRONIZATION
// ----------------------------------------------------------------------

window.onload = async () => {
    await fetchAllData();
    
    if (!Array.isArray(agents)) agents = [];
    if (!Array.isArray(mockCustomers)) mockCustomers = [];
    
    // Run authentication
    const isAuthenticated = await authenticateUser();
    if (!isAuthenticated) return; // Stop execution if auth fails

    // Enforce UI restrictions
    enforceRolePermissions();
    
    initCurrentPage();
};

async function fetchAllData() {
    try {
        // Fetch Agents
        const resAgents = await fetch(`${API_BASE}/agents`);
        if (!resAgents.ok) throw new Error(`API Error: ${resAgents.status}`);
        const agentsData = await resAgents.json();
        agents = Array.isArray(agentsData) ? agentsData : [];

        // Fetch Campaigns
        const resCamps = await fetch(`${API_BASE}/campaigns`);
        if (!resCamps.ok) throw new Error(`API Error: ${resCamps.status}`);
        const campaigns = await resCamps.json();
        if (Array.isArray(campaigns)) {
            campaigns.forEach(c => {
                campaignConfigs[c.name] = c.type;
            });
        }

        // Fetch Customers
        const resCust = await fetch(`${API_BASE}/customers`);
        if (!resCust.ok) throw new Error(`API Error: ${resCust.status}`);
        const custData = await resCust.json();
        mockCustomers = Array.isArray(custData) ? custData : [];

        recalculateGlobalStats();
    } catch (err) {
        console.error("API Error - Could not fetch data:", err);
        agents = []; 
        mockCustomers = [];
        showAppAlert("Could not connect to the database. The API returned an error.", "Connection Error");
    }
}

function recalculateGlobalStats() {
    globalStats = { totalCalls: 0, connected: 0, recovered: 0, outcomes: {} };
    
    // Tally up totals from agent profiles
    agents.forEach(a => {
        globalStats.totalCalls += (a.callsMade || 0);
        globalStats.connected += (a.connected || 0);
        globalStats.recovered += (a.conversion || 0);
    });
    
    // Tally up outcomes from worked customers
    mockCustomers.forEach(c => {
        if (String(c.worked).toUpperCase() === 'TRUE' && c.outcome) {
            globalStats.outcomes[c.outcome] = (globalStats.outcomes[c.outcome] || 0) + 1;
        }
    });
}

function saveAppState() {
    localStorage.setItem('LOGGED_IN_AGENT', LOGGED_IN_AGENT);
}

function initCurrentPage() {
    renderAgentDropdown();
    if (typeof updateCampaignDropdowns === 'function') updateCampaignDropdowns();
    if (typeof updateAnalyticsUI === 'function') updateAnalyticsUI();
    if (typeof renderCampaignList === 'function') renderCampaignList();
    renderAgentQueue();
    setActiveNavLink();
}

function setActiveNavLink() {
  const page = document.body.dataset.page || 'workspace';
  document.querySelectorAll('.nav-btn').forEach(btn => {
    const isActive = btn.dataset.page === page;
    btn.classList.toggle('bg-white/40', isActive);
    const indicator = btn.querySelector('.active-indicator');
    if (indicator) indicator.classList.toggle('hidden', !isActive);
  });
}

// ----------------------------------------------------------------------
// MODAL & UI UTILITIES
// ----------------------------------------------------------------------

function ensureAppModal() {
  if (document.getElementById('app-modal-backdrop')) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="app-modal-backdrop" class="app-modal-backdrop fixed inset-0 z-[70] hidden items-center justify-center p-4 bg-brandDark/40 backdrop-blur-sm">
      <div class="app-modal-card glass-panel w-full max-w-md rounded-2xl p-6 shadow-2xl bg-white/90" role="dialog" aria-modal="true" aria-labelledby="app-modal-title">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 id="app-modal-title" class="text-lg font-semibold text-brandDark"></h2>
            <p id="app-modal-message" class="text-sm text-brandDark/70 mt-2 whitespace-pre-line"></p>
          </div>
          <button type="button" id="app-modal-close" class="text-brandDark/50 hover:text-red-500 transition" aria-label="Close dialog"><i class="fa-solid fa-xmark text-xl"></i></button>
        </div>
        <input id="app-modal-input" type="text" class="glass-input rounded-lg w-full px-3 py-2 text-sm hidden mt-5" autocomplete="off">
        <div class="flex justify-end gap-3 mt-6">
          <button type="button" id="app-modal-cancel" class="px-4 py-2 text-sm font-medium text-brandDark/70 hover:text-brandDark transition hidden">Cancel</button>
          <button type="button" id="app-modal-confirm" class="bg-brandDark hover:bg-slate-800 text-white font-medium px-5 py-2 rounded-lg shadow-md transition text-sm">OK</button>
        </div>
      </div>
    </div>`);
  const backdrop = document.getElementById('app-modal-backdrop');
  document.getElementById('app-modal-close').onclick = () => finishAppModal(null);
  document.getElementById('app-modal-cancel').onclick = () => finishAppModal(null);
  document.getElementById('app-modal-confirm').onclick = () => {
    const input = document.getElementById('app-modal-input');
    finishAppModal(input.classList.contains('hidden') ? true : input.value);
  };
  backdrop.onclick = event => { if (event.target === backdrop) finishAppModal(null); };
}

function finishAppModal(value) {
  const modal = activeAppModal;
  activeAppModal = null;
  document.getElementById('app-modal-backdrop')?.classList.add('hidden');
  document.getElementById('app-modal-backdrop')?.classList.remove('flex');
  if (modal) modal.resolve(value);
}

function showAppModal(message, options = {}) {
  ensureAppModal();
  if (activeAppModal) finishAppModal(null);
  const backdrop = document.getElementById('app-modal-backdrop');
  const input = document.getElementById('app-modal-input');
  document.getElementById('app-modal-title').innerText = options.title || 'OSA Call Center';
  document.getElementById('app-modal-message').innerText = message;
  input.value = '';
  input.placeholder = options.placeholder || '';
  input.classList.toggle('hidden', !options.input);
  document.getElementById('app-modal-cancel').classList.toggle('hidden', !options.input);
  document.getElementById('app-modal-confirm').innerText = options.input ? 'Save' : 'OK';
  backdrop.classList.remove('hidden');
  backdrop.classList.add('flex');
  return new Promise(resolve => {
    activeAppModal = { resolve };
    if (options.input) setTimeout(() => input.focus(), 0);
  });
}

function showAppAlert(message, title) {
  return showAppModal(message, { title });
}

window.addCustomOption = async function(selectId) {
  const val = await showAppModal("Enter custom option:", { title: "Add Custom Option", input: true });
  if (val && typeof val === 'string' && val.trim()) {
    const sel = document.getElementById(selectId);
    if (sel) {
      const opt = document.createElement('option');
      opt.value = val.trim();
      opt.textContent = val.trim();
      opt.selected = true;
      sel.appendChild(opt);
    }
  }
};

// ----------------------------------------------------------------------
// GLOBAL MODAL & DRAWER CONTROLLERS (For inline HTML events)
// ----------------------------------------------------------------------

window.openAddCampaignModal = function(e) {
  if (e) e.stopPropagation();
  const backdrop = document.getElementById('add-campaign-modal-backdrop');
  const modal = document.getElementById('add-campaign-modal');
  if (backdrop && modal) {
    backdrop.classList.remove('hidden');
    setTimeout(() => {
      backdrop.classList.remove('opacity-0');
      modal.classList.remove('scale-95');
    }, 10);
  }
};

window.closeAddCampaignModal = function(e) {
  if (e) e.stopPropagation();
  const backdrop = document.getElementById('add-campaign-modal-backdrop');
  const modal = document.getElementById('add-campaign-modal');
  if (backdrop && modal) {
    backdrop.classList.add('opacity-0');
    modal.classList.add('scale-95');
    setTimeout(() => backdrop.classList.add('hidden'), 300);
  }
};

// --- CAMPAIGNS PAGE VIEW CONTROLLERS ---

window.openShiftManager = function(e) {
  if (e) e.stopPropagation();
  
  const listState = document.getElementById('campaign-list-state');
  const shiftState = document.getElementById('shift-manager-state');
  
  if (listState && shiftState) {
    // Hide the campaign list table
    listState.classList.add('hidden');
    // Show the shift manager allocation screen
    shiftState.classList.remove('hidden');
    
    // Refresh the dropdowns when the screen opens
    if (typeof renderShiftManager === 'function') {
      renderShiftManager();
    }
  }
};

window.closeSecondaryState = function(e) {
  if (e) e.stopPropagation();
  
  const listState = document.getElementById('campaign-list-state');
  const shiftState = document.getElementById('shift-manager-state');
  const detailsState = document.getElementById('campaign-details-state');
  
  // Hide all secondary screens
  if (shiftState) shiftState.classList.add('hidden');
  if (detailsState) detailsState.classList.add('hidden');
  
  // Bring back the main campaign list
  if (listState) listState.classList.remove('hidden');
};

window.closeShiftManager = function(e) {
  if (e) e.stopPropagation();
  const backdrop = document.getElementById('shift-manager-modal-backdrop');
  const modal = document.getElementById('shift-manager-modal');
  if (backdrop && modal) {
    backdrop.classList.add('opacity-0');
    modal.classList.add('scale-95');
    setTimeout(() => backdrop.classList.add('hidden'), 300);
  }
};

window.openCustomerDrawer = function(eOrId) {
  if (typeof eOrId === 'object' && eOrId !== null && eOrId.stopPropagation) {
    eOrId.stopPropagation();
  } else if (typeof eOrId === 'number' || typeof eOrId === 'string') {
    const c = mockCustomers.find(x => x.id === Number(eOrId));
    if (c) {
      document.getElementById('drawer-initial').innerText = c.name ? c.name.charAt(0).toUpperCase() : '-';
      document.getElementById('drawer-name').innerText = c.name || 'Unknown';
      document.getElementById('drawer-phone').innerText = c.phone || '--';
      document.getElementById('drawer-campaign').innerText = c.campaign || '--';
      document.getElementById('drawer-balance').innerText = c.balance || '0';
      document.getElementById('drawer-agent').innerText = c.agentId || '--';
      document.getElementById('drawer-outcome').innerText = c.outcome || '--';
      document.getElementById('drawer-status').innerText = c.status || '--';
      document.getElementById('drawer-sector').innerText = c.sector || '--';
      document.getElementById('drawer-branch').innerText = c.branch || '--';
    }
  }

  const backdrop = document.getElementById('customer-drawer-backdrop');
  const drawer = document.getElementById('customer-drawer');
  if (backdrop && drawer) {
    backdrop.classList.remove('hidden');
    setTimeout(() => {
      backdrop.classList.remove('opacity-0');
      drawer.classList.remove('translate-x-full');
    }, 10);
  }
};


window.closeCustomerDrawer = function(e) {
  if (e) e.stopPropagation();
  const backdrop = document.getElementById('customer-drawer-backdrop');
  const drawer = document.getElementById('customer-drawer');
  if (backdrop && drawer) {
    backdrop.classList.add('opacity-0');
    drawer.classList.add('translate-x-full');
    setTimeout(() => backdrop.classList.add('hidden'), 300);
  }
};

window.switchDrawerTab = function(tabName, element) {
  const tabs = document.querySelectorAll('.drawer-content');
  tabs.forEach(tab => tab.classList.add('hidden'));
  
  const target = document.getElementById(`drawer-${tabName}`);
  if (target) target.classList.remove('hidden');

  const btns = document.querySelectorAll('.drawer-tab');
  btns.forEach(btn => {
    btn.classList.remove('text-brandAmber', 'border-brandAmber');
    btn.classList.add('text-brandDark/50', 'border-transparent');
  });

  if (element) {
    element.classList.remove('text-brandDark/50', 'border-transparent');
    element.classList.add('text-brandAmber', 'border-brandAmber');
  }
};

window.switchTeamLeaderTab = function(tabName, element) {
  const tabs = document.querySelectorAll('.teamleader-tab-content');
  tabs.forEach(tab => tab.classList.add('hidden'));

  const selectedTab = document.getElementById(`tl-tab-${tabName}`);
  if (selectedTab) selectedTab.classList.remove('hidden');

  const buttons = document.querySelectorAll('.teamleader-tab-btn');
  buttons.forEach(btn => {
    btn.classList.remove('border-brandAmber', 'text-brandAmber');
    btn.classList.add('border-transparent', 'text-brandDark/50');
  });

  if (element) {
    element.classList.remove('border-transparent', 'text-brandDark/50');
    element.classList.add('border-brandAmber', 'text-brandAmber');
  }
};

window.parseCSV = function(text) {
  const lines = text.split(/\r\n|\n/);
  if (lines.length === 0) return [];

  const headers = lines[0].split(',').map(h => h.trim().replace(/^["']|["']$/g, ''));
  const results = [];

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    const values = line.split(',').map(v => v.trim().replace(/^["']|["']$/g, ''));
    const row = {};

    headers.forEach((header, index) => {
      row[header.toLowerCase()] = values[index] || '';
      row[header] = values[index] || '';
    });

    if (!row.id) row.id = i;
    results.push(row);
  }

  return results;
};

// ----------------------------------------------------------------------
// AGENT MANAGEMENT
// ----------------------------------------------------------------------

async function addAgent(e) {
    e.preventDefault();
    const nameInput = e.target.querySelector('input[type="text"]') || document.getElementById('new-agent-name');
    const teamInput = e.target.querySelector('select') || document.getElementById('new-agent-team');
    const name = nameInput.value.trim();
    
    if (agents.find(a => a.name.toLowerCase() === name.toLowerCase())) {
        showAppAlert("An agent with this name already exists.", "Agent already exists");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, team: teamInput.value, status: 'Active' })
        });

        if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
        
        nameInput.value = '';
        await fetchAllData(); 
        
        renderAgentDropdown();
        renderShiftManager();
        renderTeamLeaderWorkspace();
        renderCampaignAgentSelector();
        updateAnalyticsUI();

    } catch (err) {
        console.error("Add Agent error:", err);
        showAppAlert("Failed to save agent to the database.", "Network Error");
    }
}

async function updateAgentStatus(index, status) {
  const agent = agents[index];
  if (!agent) return;

  try {
      const res = await fetch(`${API_BASE}/agents/status`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: agent.name, status: status })
      });

      if (!res.ok) throw new Error(`Status ${res.status}`);
      
      await fetchAllData(); 
      
      renderShiftManager();
      renderTeamLeaderWorkspace();
      renderCampaignAgentSelector();
      updateAnalyticsUI();
      if (isClockedIn) renderAgentQueue();
  } catch (err) {
      showAppAlert("Failed to update agent status.", "Error");
  }
}

function renderAgentDropdown() {
  const sel = document.getElementById('current-agent-select');
  if (!sel) return;
  
  if (!Array.isArray(agents)) agents = [];

  if (agents.length === 0) {
    sel.innerHTML = '<option value="">No Agents Available</option>';
    LOGGED_IN_AGENT = null;
  } else {
    sel.innerHTML = agents.map(a => `<option value="${escapeHtml(a.name)}" ${a.name === LOGGED_IN_AGENT ? 'selected' : ''}>${escapeHtml(a.name)}</option>`).join('');
    if (!LOGGED_IN_AGENT || !agents.find(a => a.name === LOGGED_IN_AGENT)) {
        LOGGED_IN_AGENT = agents[0].name;
        sel.value = LOGGED_IN_AGENT;
    }
  }
  
  saveAppState();
  if (isClockedIn) renderAgentQueue();
  updateWorkspaceStats();
}

function switchActiveAgent() {
  const sel = document.getElementById('current-agent-select');
  if (sel) LOGGED_IN_AGENT = sel.value;
  saveAppState();
  if (isClockedIn) renderAgentQueue();
  updateWorkspaceStats();
  
  const activeCall = document.getElementById('active-call-panel');
  if (activeCall) activeCall.classList.add('hidden');
  if (isClockedIn && document.getElementById('empty-call-state')) {
    document.getElementById('empty-call-state').classList.remove('hidden');
  }
  activeCustomerId = null;
}

// ----------------------------------------------------------------------
// WORKSPACE & DISPOSITIONS
// ----------------------------------------------------------------------

async function submitDisposition(e) {
  e.preventDefault();
  if (!activeCustomerId) return;

  const submitBtn = e.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.innerHTML;
  submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Saving...';
  
  const outcome = document.getElementById('disp-outcome').value;
  const activeCustomer = mockCustomers.find(x => x.id === activeCustomerId);
  const campType = campaignConfigs[activeCustomer?.campaign] || 'defaulted';
  
  let dispositionSaved = '';
  let amountRec = 0;
  let comments = document.getElementById('disp-comments')?.value.trim() || '';
  let businessStatus = document.getElementById('disp-business')?.value || '';
  
  if (campType === 'active_no_loan' || campType === 'dormant') {
      dispositionSaved = document.getElementById('disp-response')?.value || (outcome === 'Answered' ? '' : 'Pending Callback');
  } else {
      dispositionSaved = document.getElementById('disp-status')?.value || (outcome === 'Answered' ? '' : 'Pending Callback');
      if (dispositionSaved === 'Promise to Pay (PTP)' || dispositionSaved === 'Settled') {
          amountRec = parseFloat(document.getElementById('input-amount')?.value) || 0;
      }
  }

  const payload = {
      customerId: activeCustomerId,
      outcome: outcome,
      status: dispositionSaved,
      amountRec: amountRec,
      agentName: LOGGED_IN_AGENT,
      comments: comments,
      businessStatus: businessStatus
  };

  try {
      const res = await fetch(`${API_BASE}/disposition`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error(`Status ${res.status}`);
      
      await fetchAllData();

      activeCustomerId = null;
      updateWorkspaceStats();
      if (document.getElementById('active-call-panel')) document.getElementById('active-call-panel').classList.add('hidden');
      if (document.getElementById('empty-call-state')) document.getElementById('empty-call-state').classList.remove('hidden');
      renderAgentQueue();
      
      updateAnalyticsUI();
      renderShiftManager();
      renderTeamLeaderWorkspace();
      
      const campaignsView = document.getElementById('view-campaigns');
      if (campaignsView && !campaignsView.classList.contains('hidden')) {
          renderCampaignList();
      }

      submitBtn.innerHTML = originalText;
      showAppAlert("Disposition saved to Google Sheets!", "Success");
  } catch (err) {
      submitBtn.innerHTML = originalText;
      showAppAlert("Failed to save disposition to database.", "Network Error");
  }
}

// ----------------------------------------------------------------------
// CAMPAIGNS & ALLOCATION
// ----------------------------------------------------------------------

async function submitAddCampaign(e) {
  e.preventDefault();
  const submitBtn = e.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.innerHTML;
  
  const campaignName = document.getElementById('new-campaign-name').value.trim(); 
  const campaignType = document.getElementById('new-campaign-type').value; 
  const priority = document.getElementById('new-campaign-priority')?.value || 'medium'; 
  const startDate = document.getElementById('new-campaign-start')?.value || ''; 
  const endDate = document.getElementById('new-campaign-end')?.value || ''; 
  const fileInput = document.getElementById('csv-file-input');
  const file = fileInput ? fileInput.files[0] : null;

  if (!file) { showAppAlert("Please attach a CSV file.", "CSV file required"); return; }
  
  submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Processing CSV...';
  const reader = new FileReader();
  
  reader.onload = async function(event) {
    const csvText = event.target.result;
    const rawCustomers = parseCSV(csvText);
    
    if (rawCustomers.length === 0) {
      showAppAlert("CSV seems empty or invalid. Ensure it has a header row.", "Invalid CSV");
      submitBtn.innerHTML = originalText;
      return;
    }

    // FIX FOR 422 ERROR: Strictly map CSV rows to match the FastAPI Pydantic schema
    const formattedCustomers = rawCustomers.map((row, index) => ({
        id: parseInt(row.id) || (index + 1),
        name: row.name || row.Name || "Unknown",
        phone: String(row.phone || row.Phone || ""),
        branch: row.branch || row.Branch || "Not Specified",
        sector: row.sector || row.Sector || "Not Specified",
        balance: String(row.balance || row.Balance || "0"),
        campaign: campaignName // Required by backend API
    }));

    const payload = {
        name: campaignName,
        type: campaignType,
        priority: priority,
        startDate: startDate,
        endDate: endDate,
        customers: formattedCustomers
    };

    try {
        const res = await fetch(`${API_BASE}/campaigns`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) throw new Error(`Status ${res.status}`);
        
        await fetchAllData();
        
        updateCampaignDropdowns();
        renderCampaignList(); 
        
        submitBtn.innerHTML = originalText;
        closeAddCampaignModal();
        e.target.reset();
        if (document.getElementById('csv-filename')) {
          document.getElementById('csv-filename').innerText = "No file selected";
        }
        
        showAppAlert(`Success! Created campaign "${campaignName}" with ${formattedCustomers.length} imported customers.`, "Campaign created");
    } catch (err) {
        submitBtn.innerHTML = originalText;
        console.error("Campaign Creation Error:", err);
        showAppAlert("Failed to create campaign in Google Sheets.", "Error");
    }
  };
  reader.readAsText(file);
}
async function distributeCustomers() {
  const campEl = document.getElementById('alloc-campaign');
  const campaign = campEl ? campEl.value : '';
  if (!campaign) {
    showAppAlert("Please select a campaign from the dropdown first.", "Campaign required");
    return;
  }
  
  try {
      const res = await fetch(`${API_BASE}/distribute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaign })
      });
      const data = await res.json();
      
      await fetchAllData();
      
      renderShiftManager();
      renderCampaignAgentSelector();
      if (isClockedIn) renderAgentQueue(); 
      
      showAppAlert(data.message || `Success: Distributed ${data.assignedCount} customers.`, "Distribution Complete");
  } catch (err) {
      showAppAlert("Failed to distribute customers via backend.", "Error");
  }
}

// ----------------------------------------------------------------------
// DOM & WORKSPACE UI HELPERS
// ----------------------------------------------------------------------

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatMoney(value) {
  const numericValue = Number(String(value ?? 0).replace(/[^\d.-]/g, '')) || 0;
  return `Sh ${numericValue.toLocaleString()}`;
}

function switchWorkspaceQueueTab(tab) {
  activeWorkspaceQueueTab = tab;
  ['active', 'pending'].forEach(tabName => {
    const isActive = tabName === tab;
    const btn = document.getElementById(`ws-queue-tab-${tabName}`);
    if (!btn) return;
    btn.classList.toggle('bg-brandDark', isActive);
    btn.classList.toggle('text-white', isActive);
    btn.classList.toggle('bg-white/50', !isActive);
    btn.classList.toggle('text-brandDark/70', !isActive);
    btn.classList.toggle('border', !isActive);
    btn.classList.toggle('border-brandDark/10', !isActive);
  });
  renderAgentQueue();
}

function toggleAgentStatus(checkbox) {
  isClockedIn = checkbox.checked;
  const label = document.getElementById('clock-status-label');
  const globalText = document.getElementById('global-status-text');
  const idleMsg = document.getElementById('idle-overlay');
  const queuePanel = document.getElementById('workspace-queue');
  const emptyState = document.getElementById('empty-call-state');
  const activeCall = document.getElementById('active-call-panel');

  if (!LOGGED_IN_AGENT) {
      showAppAlert("Please create or select an active agent first.", "Agent required");
      checkbox.checked = false;
      isClockedIn = false;
      return;
  }

  if (isClockedIn) {
    if (label) { label.innerText = "Clocked In"; label.classList.add('text-green-600'); }
    if (globalText) {
      globalText.classList.replace('text-gray-500', 'text-green-700');
      globalText.innerHTML = `<span class="w-2 h-2 rounded-full bg-green-500"></span> ONLINE`;
    }
    if (idleMsg) idleMsg.classList.add('hidden');
    if (queuePanel) queuePanel.classList.remove('hidden');
    
    if (!activeCustomerId && emptyState) emptyState.classList.remove('hidden');
    renderAgentQueue();
  } else {
    if (label) { label.innerText = "Clocked Out"; label.classList.remove('text-green-600'); }
    if (globalText) {
      globalText.classList.replace('text-green-700', 'text-gray-500');
      globalText.innerHTML = `<span class="w-2 h-2 rounded-full bg-gray-400"></span> OFFLINE`;
    }
    if (idleMsg) idleMsg.classList.remove('hidden');
    if (queuePanel) queuePanel.classList.add('hidden');
    if (emptyState) emptyState.classList.add('hidden');
    if (activeCall) activeCall.classList.add('hidden');
  }
}

function renderAgentQueue() {
    const queueDiv = document.getElementById('agent-customer-list');
    if (!queueDiv || !LOGGED_IN_AGENT) return;
    const countSpan = document.getElementById('queue-count');
    queueDiv.innerHTML = '';

    const myCustomers = mockCustomers.filter(c => {
      if (activeWorkspaceQueueTab === 'pending') {
        return c.pendingReschedule && String(c.worked).toUpperCase() !== 'TRUE' && (!c.agentId || c.agentId === LOGGED_IN_AGENT);
      }
      return c.agentId === LOGGED_IN_AGENT && String(c.worked).toUpperCase() !== 'TRUE' && !c.pendingReschedule;
    });

    if (countSpan) {
      countSpan.innerText = activeWorkspaceQueueTab === 'pending'
        ? `${myCustomers.length} Pending`
        : `${myCustomers.length} Remaining`;
    }

    if (myCustomers.length === 0) {
        queueDiv.innerHTML = `<div class="p-4 text-center text-brandDark/50 text-sm font-medium">${activeWorkspaceQueueTab === 'pending' ? 'No pending callbacks.' : 'Your queue is empty.'}</div>`;
        return;
    }

    myCustomers.forEach(c => {
        queueDiv.innerHTML += `
        <div class="bg-white/80 border border-white hover:border-brandAmber/50 hover:shadow-md p-3 rounded-lg cursor-pointer transition flex flex-col gap-1" onclick="startCall(${c.id})">
            <div class="flex justify-between items-center">
                <button type="button" onclick="event.stopPropagation(); openCustomerDrawer(${c.id})" class="font-bold text-sm text-brandDark text-left hover:text-brandAmber transition">${escapeHtml(c.name)}</button>
                <i class="fa-solid fa-phone text-brandAmber text-xs"></i>
            </div>
            <div class="text-xs text-brandDark/60">${escapeHtml(c.campaign || '')}</div>
            ${c.pendingReschedule ? '<div class="text-[11px] font-bold text-amber-700">Pending callback</div>' : ''}
        </div>`;
    });
}

function startCall(id) {
  activeCustomerId = id;
  const c = mockCustomers.find(x => x.id === id);
  if (!c) return;

  if (document.getElementById('empty-call-state')) document.getElementById('empty-call-state').classList.add('hidden');
  if (document.getElementById('active-call-panel')) document.getElementById('active-call-panel').classList.remove('hidden');

  if (document.getElementById('active-name')) document.getElementById('active-name').innerText = c.name || 'Unknown';
  if (document.getElementById('active-phone')) document.getElementById('active-phone').innerText = c.phone || '--';
  if (document.getElementById('active-campaign')) document.getElementById('active-campaign').innerText = c.campaign || '--';
  if (document.getElementById('active-debt')) document.getElementById('active-debt').innerText = c.balance || '0';
  if (document.getElementById('active-branch')) document.getElementById('active-branch').innerText = `${c.branch || '--'} / ${c.sector || '--'}`;
  
  if (!c.agentId) c.agentId = LOGGED_IN_AGENT;
  c.pendingReschedule = false;
  
  const form = document.getElementById('disposition-form');
  if (form) form.reset();
  
  const campType = campaignConfigs[c.campaign] || 'defaulted'; 
  const resContainer = document.getElementById('container-customer-response');
  const statContainer = document.getElementById('container-account-status');
  
  if (resContainer && statContainer) {
    if (campType === 'active_no_loan' || campType === 'dormant') {
       resContainer.classList.remove('hidden');
       if (document.getElementById('disp-response')) document.getElementById('disp-response').required = true;
       statContainer.classList.add('hidden');
       if (document.getElementById('disp-status')) document.getElementById('disp-status').required = false;
    } else {
       statContainer.classList.remove('hidden');
       if (document.getElementById('disp-status')) document.getElementById('disp-status').required = true;
       resContainer.classList.add('hidden');
       if (document.getElementById('disp-response')) document.getElementById('disp-response').required = false;
    }
  }

  handleOutcomeChangeGlass(); 
}

function handleOutcomeChangeGlass() {
  const statusEl = document.getElementById('disp-status');
  const outcomeEl = document.getElementById('disp-outcome');
  const amtContainer = document.getElementById('dynamic-amount');
  const amtInput = document.getElementById('input-amount');
  const responseInput = document.getElementById('disp-response');
  const activeCustomer = mockCustomers.find(x => x.id === activeCustomerId);
  const campType = campaignConfigs[activeCustomer?.campaign] || 'defaulted';

  const status = statusEl ? statusEl.value : '';
  const outcome = outcomeEl ? outcomeEl.value : '';

  const needsAnsweredDisposition = !outcome || outcome === 'Answered';
  if (responseInput && (campType === 'active_no_loan' || campType === 'dormant')) {
    responseInput.required = needsAnsweredDisposition;
  } else if (statusEl) {
    statusEl.required = needsAnsweredDisposition;
  }
  
  if (amtContainer && amtInput) {
    if (needsAnsweredDisposition && (status === 'Promise to Pay (PTP)' || status === 'Settled')) {
      amtContainer.classList.remove('hidden');
      amtInput.required = true;
    } else {
      amtContainer.classList.add('hidden');
      amtInput.required = false;
    }
  }
}

function updateWorkspaceStats() {
   if (!document.getElementById('ws-stats-calls')) return;
   const agent = agents.find(a => a.name === LOGGED_IN_AGENT);
   if (agent) {
      document.getElementById('ws-stats-calls').innerText = agent.callsMade || 0;
      document.getElementById('ws-stats-conv').innerText = `Sh ${(agent.conversion || 0).toLocaleString()}`;
   } else {
      document.getElementById('ws-stats-calls').innerText = '0';
      document.getElementById('ws-stats-conv').innerText = 'Sh 0';
   }
}

function updateAnalyticsUI() {
    if (!document.getElementById('dash-total-calls')) return;
    document.getElementById('dash-total-calls').innerText = globalStats.totalCalls;
    if (document.getElementById('dash-recovered')) {
      document.getElementById('dash-recovered').innerText = `Sh ${globalStats.recovered.toLocaleString()}`;
    }
    
    let connRate = globalStats.totalCalls === 0 ? 0 : Math.round((globalStats.connected / globalStats.totalCalls) * 100);
    if (document.getElementById('dash-connection')) {
      document.getElementById('dash-connection').innerText = `${connRate}%`;
    }
    
    let activeAgt = agents.filter(a => a.status === 'Active').length;
    if (document.getElementById('dash-active-agents')) {
      document.getElementById('dash-active-agents').innerText = `${activeAgt} / ${agents.length}`;
    }

    const lbBody = document.getElementById('dash-leaderboard');
    if (lbBody) {
      lbBody.innerHTML = '';
      let sortedAgents = [...agents].sort((a,b) => (b.callsMade || 0) - (a.callsMade || 0));
      sortedAgents.forEach(a => {
          if ((a.callsMade || 0) > 0) {
              lbBody.innerHTML += `
              <tr class="border-b border-brandDark/5">
                  <td class="py-2 font-bold">${escapeHtml(a.name)}</td>
                  <td class="py-2 text-right">${a.callsMade || 0}</td>
                  <td class="py-2 text-right font-bold text-green-700">${a.connected || 0}</td>
                  <td class="py-2 text-right font-bold">Sh ${(a.conversion || 0).toLocaleString()}</td>
              </tr>`;
          }
      });
      if (globalStats.totalCalls === 0) {
        lbBody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-brandDark/50 italic">No calls made yet</td></tr>`;
      }
    }

    const outcomesDiv = document.getElementById('dash-outcomes');
    if (outcomesDiv) {
      outcomesDiv.innerHTML = '';
      if (globalStats.totalCalls > 0) {
          const colorMap = { "Answered": "bg-green-500", "Unanswered": "bg-yellow-500", "Offline": "bg-gray-500", "Third party": "bg-blue-500", "Voicemail": "bg-amber-700" };
          for (let key in globalStats.outcomes) {
              let count = globalStats.outcomes[key];
              if (count === 0) continue; 
              let perc = Math.round((count / globalStats.totalCalls) * 100);
              let colorClass = colorMap[key] || "bg-brandDark";
              outcomesDiv.innerHTML += `
              <div>
                  <div class="flex justify-between mb-1"><span>${key}</span> <span>${perc}%</span></div>
                  <div class="w-full bg-brandDark/10 rounded-full h-2">
                      <div class="${colorClass} h-2 rounded-full" style="width: ${perc}%"></div>
                  </div>
              </div>`;
          }
      } else {
           outcomesDiv.innerHTML = `<p class="text-brandDark/50 italic font-medium">No outcomes logged.</p>`;
      }
    }
}