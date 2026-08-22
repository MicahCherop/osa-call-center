// --- STEP 4: FRONTEND API INTEGRATION ---
const API_BASE = '/api';

// Persistent Local UI States
let LOGGED_IN_AGENT = localStorage.getItem('LOGGED_IN_AGENT') || null;
let isClockedIn = false;
let activeCustomerId = null;
let activeWorkspaceQueueTab = 'active';
let activeAppModal = null;

// Dynamic Data States (Populated from Google Sheets via FastAPI)
let mockCustomers = [];
let campaignConfigs = {};
let agents = [];
let globalStats = {
  totalCalls: 0,
  connected: 0,
  recovered: 0,
  outcomes: {}
};

// ----------------------------------------------------------------------
// DATA FETCHING & SYNCHRONIZATION
// ----------------------------------------------------------------------

window.onload = async () => {
    // 1. Fetch data from backend on load
    await fetchAllData();
    
    // 2. Fallback safeguards to prevent JS crashes
    if (!Array.isArray(agents)) agents = [];
    if (!Array.isArray(mockCustomers)) mockCustomers = [];
    
    // 3. Initialize UI
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

function renderAgentDropdown() {
  const sel = document.getElementById('current-agent-select');
  if (!sel) return;
  
  // Failsafe: Ensure agents is always an array
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
  if(isClockedIn) renderAgentQueue();
  updateWorkspaceStats();
}
window.openAddCampaignModal = function() {
  const modal = document.getElementById('add-campaign-modal');
  const backdrop = document.getElementById('add-campaign-modal-backdrop');
  if (modal && backdrop) {
    backdrop.classList.remove('hidden');
    setTimeout(() => {
      backdrop.classList.remove('opacity-0');
      modal.classList.remove('scale-95');
    }, 10);
  }
};

window.closeAddCampaignModal = function() {
  const modal = document.getElementById('add-campaign-modal');
  const backdrop = document.getElementById('add-campaign-modal-backdrop');
  if (modal && backdrop) {
    backdrop.classList.add('opacity-0');
    modal.classList.add('scale-95');
    setTimeout(() => {
      backdrop.classList.add('hidden');
    }, 300);
  }
};
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

// Keep the UI state saving strictly to UI preferences (not database data)
function saveAppState() {
    localStorage.setItem('LOGGED_IN_AGENT', LOGGED_IN_AGENT);
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

function initCurrentPage() {
    renderAgentDropdown();
    //renderShiftManager();
    renderTeamLeaderWorkspace();
    updateCampaignDropdowns();
    updateAnalyticsUI();
    renderCampaignList();
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
// --- GLOBAL MODAL CONTROLLERS ---

// 1. Add Campaign Modal
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

// 2. Customer Profile Drawer
window.openCustomerDrawer = function(e) {
  if (e) e.stopPropagation();
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

// 3. Customer Drawer Tab Switching
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
// ----------------------------------------------------------------------
// AGENT MANAGEMENT (POST / PUT to API)
// ----------------------------------------------------------------------

async function addAgent(e) {
    e.preventDefault();
    const nameInput = e.target.querySelector('input[type="text"]') || document.getElementById('new-agent-name');
    const teamInput = e.target.querySelector('select') || document.getElementById('new-agent-team');
    const name = nameInput.value.trim();
    
    if(agents.find(a => a.name.toLowerCase() === name.toLowerCase())) {
        showAppAlert("An agent with this name already exists.", "Agent already exists");
        return;
    }

    try {
        await fetch(`${API_BASE}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, team: teamInput.value, status: 'Active' })
        });
        
        nameInput.value = '';
        await fetchAllData(); // Refresh UI with DB Data
        
        renderShiftManager();
        renderTeamLeaderWorkspace();
        renderCampaignAgentSelector();
        renderAgentDropdown();
        updateAnalyticsUI();
    } catch (err) {
        showAppAlert("Failed to save agent to the database.", "Network Error");
    }
}
window.switchTeamLeaderTab = function(tabName, element) {
  // Hide all tab contents
  const tabs = document.querySelectorAll('.teamleader-tab-content');
  tabs.forEach(tab => tab.classList.add('hidden'));

  // Show selected tab content
  const selectedTab = document.getElementById(`tl-tab-${tabName}`);
  if (selectedTab) {
    selectedTab.classList.remove('hidden');
  }

  // Update active UI styles on buttons
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
async function updateAgentStatus(index, status) {
  const agent = agents[index];
  if (!agent) return;

  try {
      await fetch(`${API_BASE}/agents/status`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: agent.name, status: status })
      });
      
      await fetchAllData(); // Refresh UI with DB Data
      
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
  
  // Failsafe: Ensure agents is always an array
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
  if(isClockedIn) renderAgentQueue();
  updateWorkspaceStats();
}

function switchActiveAgent() {
  LOGGED_IN_AGENT = document.getElementById('current-agent-select').value;
  saveAppState();
  if(isClockedIn) renderAgentQueue();
  updateWorkspaceStats();
  
  document.getElementById('active-call-panel').classList.add('hidden');
  if (isClockedIn) document.getElementById('empty-call-state').classList.remove('hidden');
  activeCustomerId = null;
}

// ----------------------------------------------------------------------
// WORKSPACE & DISPOSITIONS (POST to API)
// ----------------------------------------------------------------------

async function submitDisposition(e) {
  e.preventDefault();
  if(!activeCustomerId) return;

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
      dispositionSaved = document.getElementById('disp-response').value || (outcome === 'Answered' ? '' : 'Pending Callback');
  } else {
      dispositionSaved = document.getElementById('disp-status').value || (outcome === 'Answered' ? '' : 'Pending Callback');
      if (dispositionSaved === 'Promise to Pay (PTP)' || dispositionSaved === 'Settled') {
          amountRec = parseFloat(document.getElementById('input-amount').value) || 0;
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
      await fetch(`${API_BASE}/disposition`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
      });
      
      await fetchAllData(); // Refresh UI with DB Data

      activeCustomerId = null;
      updateWorkspaceStats();
      document.getElementById('active-call-panel').classList.add('hidden');
      document.getElementById('empty-call-state').classList.remove('hidden');
      renderAgentQueue();
      
      updateAnalyticsUI();
      renderShiftManager();
      renderTeamLeaderWorkspace();
      
      const campaignsView = document.getElementById('view-campaigns');
      if (campaignsView && !campaignsView.classList.contains('hidden')){
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
// CAMPAIGNS & ALLOCATION (POST to API)
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
  const file = fileInput.files[0];

  if(!file) { showAppAlert("Please attach a CSV file.", "CSV file required"); return; }
  
  submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Processing CSV...';
  const reader = new FileReader();
  
  reader.onload = async function(event) {
    const csvText = event.target.result;
    const newCustomers = parseCSV(csvText);
    
    if(newCustomers.length === 0) {
      showAppAlert("CSV seems empty or invalid. Ensure it has a header row.", "Invalid CSV");
      submitBtn.innerHTML = originalText;
      return;
    }

    const payload = {
        name: campaignName,
        type: campaignType,
        priority: priority,
        startDate: startDate,
        endDate: endDate,
        customers: newCustomers
    };

    try {
        await fetch(`${API_BASE}/campaigns`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        await fetchAllData(); // Refresh UI with DB Data
        
        updateCampaignDropdowns();
        renderCampaignList(); 
        
        submitBtn.innerHTML = originalText;
        closeAddCampaignModal();
        e.target.reset();
        document.getElementById('csv-filename').innerText = "No file selected";
        
        showAppAlert(`Success! Created campaign "${campaignName}" with ${newCustomers.length} imported customers.\n\nNow, go to the Shift Manager to allocate them!`, "Campaign created");
    } catch (err) {
        submitBtn.innerHTML = originalText;
        showAppAlert("Failed to create campaign in Google Sheets.", "Error");
    }
  };
  reader.readAsText(file);
}

async function distributeCustomers() {
  const campaign = document.getElementById('alloc-campaign').value;
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
      
      await fetchAllData(); // Refresh UI with DB Data
      
      renderShiftManager();
      renderCampaignAgentSelector();
      if(isClockedIn) renderAgentQueue(); 
      
      showAppAlert(data.message || `Success: Distributed ${data.assignedCount} customers.`, "Distribution Complete");
  } catch (err) {
      showAppAlert("Failed to distribute customers via backend.", "Error");
  }
}

// ----------------------------------------------------------------------
// REMAINING UI/DOM HELPER FUNCTIONS
// (These keep your modal and queue toggling functional without changes)
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
      showAppAlert("Please create an agent in the Shift Manager first.", "Agent required");
      checkbox.checked = false;
      isClockedIn = false;
      return;
  }

  if (isClockedIn) {
    label.innerText = "Clocked In";
    label.classList.add('text-green-600');
    globalText.classList.replace('text-gray-500', 'text-green-700');
    globalText.innerHTML = `<span class="w-2 h-2 rounded-full bg-green-500"></span> ONLINE`;
    idleMsg.classList.add('hidden');
    queuePanel.classList.remove('hidden');
    
    if(!activeCustomerId) emptyState.classList.remove('hidden');
    renderAgentQueue();
  } else {
    label.innerText = "Clocked Out";
    label.classList.remove('text-green-600');
    globalText.classList.replace('text-green-700', 'text-gray-500');
    globalText.innerHTML = `<span class="w-2 h-2 rounded-full bg-gray-400"></span> OFFLINE`;
    idleMsg.classList.remove('hidden');
    queuePanel.classList.add('hidden');
    emptyState.classList.add('hidden');
    activeCall.classList.add('hidden');
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
    countSpan.innerText = activeWorkspaceQueueTab === 'pending'
      ? `${myCustomers.length} Pending`
      : `${myCustomers.length} Remaining`;

    if(myCustomers.length === 0) {
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
            <div class="text-xs text-brandDark/60">${c.campaign}</div>
            ${c.pendingReschedule ? '<div class="text-[11px] font-bold text-amber-700">Pending callback</div>' : ''}
        </div>`;
    });
}

function startCall(id) {
  activeCustomerId = id;
  const c = mockCustomers.find(x => x.id === id);
  if(!c) return;

  document.getElementById('empty-call-state').classList.add('hidden');
  document.getElementById('active-call-panel').classList.remove('hidden');

  document.getElementById('active-name').innerText = c.name;
  document.getElementById('active-phone').innerText = c.phone;
  document.getElementById('active-campaign').innerText = c.campaign;
  document.getElementById('active-debt').innerText = c.balance;
  document.getElementById('active-branch').innerText = `${c.branch} / ${c.sector}`;
  
  if (!c.agentId) c.agentId = LOGGED_IN_AGENT;
  c.pendingReschedule = false;
  
  document.getElementById('disposition-form').reset();
  
  const campType = campaignConfigs[c.campaign] || 'defaulted'; 
  const resContainer = document.getElementById('container-customer-response');
  const statContainer = document.getElementById('container-account-status');
  
  if (campType === 'active_no_loan' || campType === 'dormant') {
     resContainer.classList.remove('hidden');
     document.getElementById('disp-response').required = true;
     statContainer.classList.add('hidden');
     document.getElementById('disp-status').required = false;
  } else {
     statContainer.classList.remove('hidden');
     document.getElementById('disp-status').required = true;
     resContainer.classList.add('hidden');
     document.getElementById('disp-response').required = false;
  }

  handleOutcomeChangeGlass(); 
}

function handleOutcomeChangeGlass() {
  const status = document.getElementById('disp-status').value;
  const outcome = document.getElementById('disp-outcome').value;
  const amtContainer = document.getElementById('dynamic-amount');
  const amtInput = document.getElementById('input-amount');
  const responseInput = document.getElementById('disp-response');
  const statusInput = document.getElementById('disp-status');
  const activeCustomer = mockCustomers.find(x => x.id === activeCustomerId);
  const campType = campaignConfigs[activeCustomer?.campaign] || 'defaulted';

  const needsAnsweredDisposition = !outcome || outcome === 'Answered';
  if (campType === 'active_no_loan' || campType === 'dormant') {
    responseInput.required = needsAnsweredDisposition;
  } else {
    statusInput.required = needsAnsweredDisposition;
  }
  
  if (needsAnsweredDisposition && (status === 'Promise to Pay (PTP)' || status === 'Settled')) {
    amtContainer.classList.remove('hidden');
    amtInput.required = true;
  } else {
    amtContainer.classList.add('hidden');
    amtInput.required = false;
  }
}

function updateWorkspaceStats() {
   if (!document.getElementById('ws-stats-calls')) return;
   const agent = agents.find(a => a.name === LOGGED_IN_AGENT);
   if(agent) {
      document.getElementById('ws-stats-calls').innerText = agent.callsMade;
      document.getElementById('ws-stats-conv').innerText = `Sh ${agent.conversion.toLocaleString()}`;
   } else {
      document.getElementById('ws-stats-calls').innerText = '0';
      document.getElementById('ws-stats-conv').innerText = 'Sh 0';
   }
}

function updateAnalyticsUI() {
    if (!document.getElementById('dash-total-calls')) return;
    document.getElementById('dash-total-calls').innerText = globalStats.totalCalls;
    document.getElementById('dash-recovered').innerText = `Sh ${globalStats.recovered.toLocaleString()}`;
    
    let connRate = globalStats.totalCalls === 0 ? 0 : Math.round((globalStats.connected / globalStats.totalCalls) * 100);
    document.getElementById('dash-connection').innerText = `${connRate}%`;
    
    let activeAgt = agents.filter(a => a.status === 'Active').length;
    document.getElementById('dash-active-agents').innerText = `${activeAgt} / ${agents.length}`;

    const lbBody = document.getElementById('dash-leaderboard');
    lbBody.innerHTML = '';
    
    let sortedAgents = [...agents].sort((a,b) => b.callsMade - a.callsMade);
    sortedAgents.forEach(a => {
        if(a.callsMade > 0) {
            lbBody.innerHTML += `
            <tr class="border-b border-brandDark/5">
                <td class="py-2 font-bold">${a.name}</td>
                <td class="py-2 text-right">${a.callsMade}</td>
                <td class="py-2 text-right font-bold text-green-700">${a.connected}</td>
                <td class="py-2 text-right font-bold">Sh ${a.conversion.toLocaleString()}</td>
            </tr>`;
        }
    });
    if(globalStats.totalCalls === 0) lbBody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-brandDark/50 italic">No calls made yet</td></tr>`;

    const outcomesDiv = document.getElementById('dash-outcomes');
    outcomesDiv.innerHTML = '';
    if(globalStats.totalCalls > 0) {
        const colorMap = { "Answered": "bg-green-500", "Unanswered": "bg-yellow-500", "Offline": "bg-gray-500", "Third party": "bg-blue-500", "Voicemail": "bg-amber-700" };
        for(let key in globalStats.outcomes) {
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

// ... Additional helper functions (openCustomerDrawer, parseCSV, renderCampaignList, updateCampaignDropdowns) remain the same UI implementations as previously ...