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
const isAuthorized = enforceSecurity();

function enforceSecurity() {
    // 1. Kick unauthenticated users to login
    if (!CURRENT_USER_EMAIL && currentPage !== 'login') {
        window.location.replace('/login');
        return false;
    }
    
    // 2. Route logged-in users to their correct starting page
    if (CURRENT_USER_EMAIL && currentPage === 'login') {
        let dest = '/workspace'; 
        if (CURRENT_USER_ROLE === 'Team Leader' || CURRENT_USER_ROLE === 'Ops Manager') {
            dest = '/teamleader';
        }
        if (CURRENT_USER_ROLE === 'Admin') {
            dest = '/admin';
        }
        window.location.replace(dest);
        return false;
    }

    // 3. Strict Page Restrictions
    if (CURRENT_USER_ROLE === 'Control Agent' && currentPage !== 'workspace' && currentPage !== 'login') {
        window.location.replace('/workspace');
        return false;
    }
    
    if ((CURRENT_USER_ROLE === 'Team Leader' || CURRENT_USER_ROLE === 'Ops Manager') && (currentPage === 'workspace' || currentPage === 'admin')) {
        window.location.replace('/teamleader');
        return false;
    }

    return true;
}

window.logout = function() {
    localStorage.clear();
    window.location.replace('/login');
};


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

// ----------------------------------------------------------------------
// RENDER FUNCTIONS (Restored to fix Shift Manager and Campaigns)
// ----------------------------------------------------------------------

window.updateCampaignDropdowns = function() {
    // 1. Get the exact dropdown element we created earlier
    const allocateDropdown = document.getElementById('allocate-campaign');
    
    if (allocateDropdown) {
        // 2. Preserve the default "Select campaign..." option
        const defaultOption = '<option value="">Select campaign...</option>';
        allocateDropdown.innerHTML = defaultOption;
        
        // 3. Loop through the campaignConfigs dictionary we built in fetchAllData
        // Object.keys(campaignConfigs) gives us an array of all the campaign names!
        Object.keys(campaignConfigs).forEach(campaignName => {
            const opt = document.createElement('option');
            opt.value = campaignName;
            opt.textContent = campaignName;
            allocateDropdown.appendChild(opt);
        });
    }

    // (Optional) If you have other campaign dropdowns on the page like a filter, 
    // you can replicate the block above for those IDs too!
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
    // These run on all applicable pages
    if (typeof updateCampaignDropdowns === 'function') updateCampaignDropdowns();
    if (typeof updateAnalyticsUI === 'function') updateAnalyticsUI();
    if (typeof renderCampaignList === 'function') renderCampaignList();
    if (typeof renderAgentQueue === 'function') renderAgentQueue();
    if (typeof setActiveNavLink === 'function') setActiveNavLink();

    // Fix: Trigger specific lists based on what page you are currently viewing
    if (currentPage === 'admin' && typeof renderAdminUserList === 'function') {
        renderAdminUserList();
    }
    if (currentPage === 'teamleader' && typeof renderShiftManager === 'function') {
        renderShiftManager();
        if (typeof renderTLCustomers === 'function') renderTLCustomers();
    }
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
  // Hide all tab contents
  const tabs = document.querySelectorAll('.teamleader-tab-content');
  tabs.forEach(tab => tab.classList.add('hidden'));

  // Show selected tab content
  const selectedTab = document.getElementById(`tl-tab-${tabName}`);
  if (selectedTab) selectedTab.classList.remove('hidden');

  // Reset all buttons to inactive (white with border)
  const buttons = document.querySelectorAll('.teamleader-tab-btn');
  buttons.forEach(btn => {
    btn.className = "teamleader-tab-btn bg-white border border-brandDark/20 text-brandDark/70 hover:text-brandDark hover:border-brandDark/40 px-4 py-2 rounded-lg text-sm font-bold transition flex items-center gap-2";
  });

  // Set the clicked button to active (dark background)
  if (element) {
    element.className = "teamleader-tab-btn bg-brandDark text-white border border-transparent px-4 py-2 rounded-lg text-sm font-bold transition flex items-center gap-2";
  }
};
window.renderTLPending = function() {
    const tbody = document.getElementById('tl-pending-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    // Filter for outcomes that exist but are NOT 'Answered'
    const pendingCustomers = mockCustomers.filter(c => c.outcome && c.outcome !== 'Answered');

    if (pendingCustomers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="px-4 py-8 text-center text-brandDark/50 italic font-medium">No pending callbacks found.</td></tr>`;
        return;
    }

    pendingCustomers.forEach(c => {
        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5 hover:bg-white/40 transition">
                <td class="px-4 py-3 font-bold cursor-pointer hover:text-brandAmber" onclick="openCustomerDrawer(${c.id})">${escapeHtml(c.name)}</td>
                <td class="px-4 py-3">${escapeHtml(c.phone)}</td>
                <td class="px-4 py-3 text-brandDark/70">${escapeHtml(c.campaign)}</td>
                <td class="px-4 py-3 font-bold text-red-600">${escapeHtml(c.outcome)}</td>
                <td class="px-4 py-3 text-amber-700 font-bold">${escapeHtml(c.agentId || 'Unassigned')}</td>
            </tr>
        `;
    });
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
window.renderAdminUserList = function() {
    const tbody = document.getElementById('admin-users-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    if (!agents || agents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-8 text-center text-brandDark/50 italic">No users found in database.</td></tr>';
        return;
    }

    agents.forEach(a => {
        // Map keys dynamically to handle both uppercase/lowercase API returns
        const role = a.Role || a.role || 'Unknown';
        const name = a.Name || a.name || 'Unknown';
        const email = a.Email || a.email || '--';
        const status = a.Status || a.status || 'Inactive';

        let badgeColor = role === 'Control Agent' ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800';
        if(role === 'Ops Manager') badgeColor = 'bg-purple-100 text-purple-800';
        if(role === 'Admin') badgeColor = 'bg-red-100 text-red-800';
        
        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5">
                <td class="px-4 py-3 font-bold text-brandDark">${escapeHtml(name)}</td>
                <td class="px-4 py-3">${escapeHtml(email)}</td>
                <td class="px-4 py-3"><span class="px-2 py-1 rounded text-[11px] font-bold ${badgeColor}">${escapeHtml(role)}</span></td>
                <td class="px-4 py-3 font-bold ${status === 'Active' ? 'text-green-600' : 'text-gray-500'}">${escapeHtml(status)}</td>
            </tr>
        `;
    });
};

window.renderShiftManager = function() {
    const tbody = document.getElementById('shift-manager-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    // Safely extract Control Agents
    const controlAgents = agents.filter(a => (a.Role || a.role) === 'Control Agent');
    
    if (controlAgents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-brandDark/50 italic">No Control Agents found in database.</td></tr>';
        return;
    }

    controlAgents.forEach(a => {
        const name = a.Name || a.name || 'Unknown';
        const status = a.Status || a.status || 'Offline';
        const team = a.Team || a.team || 'General';
        
        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5">
                <td class="px-4 py-3 font-bold">${escapeHtml(name)}</td>
                <td class="px-4 py-3 text-brandDark/70">${escapeHtml(team)}</td>
                <td class="px-4 py-3 font-bold ${status === 'Clocked In' ? 'text-green-600' : 'text-gray-500'}">${escapeHtml(status)}</td>
                <td class="px-4 py-3 text-brandDark/50">--</td>
                <td class="px-4 py-3 text-brandDark/50">--</td>
            </tr>
        `;
    });
};

window.createUser = async function(e, formType) {
    e.preventDefault();

    // 1. Define your exact company domain
    const COMPANY_DOMAIN = "@4g-capital.com"; 

    // Safely grab the input elements based on the formType prefix
    const nameEl = document.getElementById(`${formType}-new-name`);
    const emailEl = document.getElementById(`${formType}-new-email`);
    
    // Check if elements exist to prevent the "null" crash
    if (!nameEl || !emailEl) {
        showAppAlert("Form error: Could not find the required input fields.", "System Error");
        return;
    }

    const name = nameEl.value.trim();
    const email = emailEl.value.trim();

    // 2. The Domain Restriction Check
    if (!email.toLowerCase().endsWith(COMPANY_DOMAIN)) {
        showAppAlert(`Users must have a ${COMPANY_DOMAIN} email address.`, "Invalid Email");
        return;
    }
    
    // If it's the admin form, grab the role dropdown. Otherwise, default to Control Agent.
    let role = 'Control Agent';
    if (formType === 'admin') {
        const roleEl = document.getElementById('admin-new-role');
        if (roleEl) role = roleEl.value;
    }
    
    // Safely verify existence with uppercase/lowercase checks
    const exists = agents.find(a => {
        const aName = a.Name || a.name;
        const aEmail = a.Email || a.email;
        return (aName && aName.toLowerCase() === name.toLowerCase()) || 
               (aEmail && aEmail.toLowerCase() === email.toLowerCase());
    });

    if (exists) {
        showAppAlert("A user with this name or email already exists.", "User Exists");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, role, status: 'Active' })
        });

        if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
        
        e.target.reset();
        await fetchAllData(); 
        
        // Refresh the lists dynamically based on the current page
        if (currentPage === 'admin' && typeof renderAdminUserList === 'function') renderAdminUserList();
        if (currentPage === 'teamleader' && typeof renderShiftManager === 'function') renderShiftManager();
        
        showAppAlert("User created successfully!", "Success");
    } catch (err) {
        showAppAlert("Failed to save user to the database.", "Network Error");
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

    // FIX: Dynamically capture ALL CSV columns while ensuring required backend fields exist
    const formattedCustomers = rawCustomers.map((row, index) => {
        const customer = { ...row }; // Captures every column dynamically
        
        // Enforce required fields
        customer.id = parseInt(row.id) || (index + 1);
        customer.name = row.name || row.Name || row.NAME || "Unknown";
        customer.phone = String(row.phone || row.Phone || row.PHONE || "");
        customer.branch = row.branch || row.Branch || row.BRANCH || "Not Specified";
        customer.sector = row.sector || row.Sector || row.SECTOR || "Not Specified";
        customer.balance = String(row.balance || row.Balance || row.BALANCE || "0");
        customer.campaign = campaignName;
        
        return customer;
    });

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
// 1. Populate the agent list when a campaign is selected
window.updateAvailableAgents = function() {
    const campaignSelect = document.getElementById('allocate-campaign');
    const agentsContainer = document.getElementById('allocate-agents-list');
    
    if (!campaignSelect || !agentsContainer) return;

    const campaign = campaignSelect.value;
    if (!campaign) {
        agentsContainer.innerHTML = '<p class="text-[13px] text-brandDark/50 italic p-3">Select a campaign to view available agents.</p>';
        return;
    }

    // Find all Control Agents safely handling case-sensitivity
    const controlAgents = agents.filter(a => {
        const role = a.Role || a.role;
        return role === 'Control Agent';
    });

    if (controlAgents.length === 0) {
        agentsContainer.innerHTML = '<p class="text-[13px] text-red-500 font-bold p-3">No Control Agents found in the database.</p>';
        return;
    }

    // Build a scrollable list of checkboxes
    let html = '<div class="max-h-40 overflow-y-auto p-2 space-y-1">';
    controlAgents.forEach(a => {
        const name = a.Name || a.name;
        html += `
            <label class="flex items-center gap-3 p-2 hover:bg-white/80 rounded-md cursor-pointer transition">
                <input type="checkbox" name="selected-agents" value="${escapeHtml(name)}" class="w-4 h-4 text-brandAmber rounded border-brandDark/20 focus:ring-brandAmber">
                <span class="text-[13px] font-bold text-brandDark">${escapeHtml(name)}</span>
            </label>
        `;
    });
    html += '</div>';

    agentsContainer.innerHTML = html;
};

// 2. Handle the form submission and distribute customers
window.submitAllocation = function(e) {
    e.preventDefault();
    
    const campaignSelect = document.getElementById('allocate-campaign');
    const campaign = campaignSelect ? campaignSelect.value : null;
    
    // Get all checked agents
    const checkedBoxes = document.querySelectorAll('input[name="selected-agents"]:checked');
    const selectedAgents = Array.from(checkedBoxes).map(box => box.value);
    
    if (!campaign || selectedAgents.length === 0) {
        showAppAlert("Please select a campaign and at least one agent.", "Allocation Failed");
        return;
    }

    // Find all unassigned customers for this specific campaign
    let unassignedCustomers = mockCustomers.filter(c => {
        const cCampaign = c.campaign || c.Campaign;
        const cAgent = c.agentId || c.AgentId;
        return cCampaign === campaign && (!cAgent || cAgent.trim() === '');
    });

    if (unassignedCustomers.length === 0) {
        showAppAlert("There are no unassigned customers available in this campaign.", "Nothing to Allocate");
        return;
    }

    // Distribute customers evenly using a round-robin approach
    unassignedCustomers.forEach((customer, index) => {
        const assignedAgent = selectedAgents[index % selectedAgents.length];
        customer.agentId = assignedAgent; 
        
        // Note: If you have an API endpoint to save this to Google Sheets, you would trigger it here.
        // e.g., fetch(`/api/customers/${customer.id}/assign`, { method: 'POST', body: JSON.stringify({ agent: assignedAgent }) })
    });

    showAppAlert(`Successfully distributed ${unassignedCustomers.length} customers across ${selectedAgents.length} agent(s)!`, "Allocation Complete");
    
    // Reset the UI
    e.target.reset();
    document.getElementById('allocate-agents-list').innerHTML = '<p class="text-[13px] text-brandDark/50 italic p-3">Select a campaign to view available agents.</p>';
    
    // Refresh any campaign lists if they are visible
    if (typeof renderCampaignList === 'function') renderCampaignList();
};
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
    // Inside updateAnalyticsUI()
    if (document.getElementById('dash-active-agents')) {
        let clockedInAgents = agents.filter(a => a.role === 'Control Agent' && a.status === 'Clocked In').length;
        let totalAgents = agents.filter(a => a.role === 'Control Agent').length;
        document.getElementById('dash-active-agents').innerText = `${clockedInAgents} / ${totalAgents}`;
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
window.renderTLCustomers = function() {
    const tbody = document.getElementById('tl-customers-tbody');
    const filterSelect = document.getElementById('tl-campaign-filter');
    if (!tbody || !filterSelect) return;

    // Populate filter dropdown if empty
    if (filterSelect.options.length <= 1) {
        const campaigns = Object.keys(campaignConfigs);
        campaigns.forEach(c => {
            filterSelect.innerHTML += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`;
        });
    }

    const selectedCampaign = filterSelect.value;
    tbody.innerHTML = '';

    // Filter customers
    const filtered = mockCustomers.filter(c => selectedCampaign === 'ALL' || c.campaign === selectedCampaign);

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="px-4 py-8 text-center text-brandDark/50 italic">No customers found for this criteria.</td></tr>`;
        return;
    }

    // Render table
    filtered.forEach(c => {
        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5 hover:bg-white/40 transition">
                <td class="px-4 py-3 font-bold cursor-pointer hover:text-brandAmber" onclick="openCustomerDrawer(${c.id})">${escapeHtml(c.name)}</td>
                <td class="px-4 py-3">${escapeHtml(c.phone)}</td>
                <td class="px-4 py-3 text-brandDark/70">${escapeHtml(c.campaign)}</td>
                <td class="px-4 py-3 font-bold text-amber-700">${escapeHtml(c.agentId || 'Unassigned')}</td>
                <td class="px-4 py-3">
                    <span class="px-2 py-1 bg-brandDark/5 rounded text-xs font-bold">${escapeHtml(c.outcome || 'Pending')}</span>
                </td>
            </tr>
        `;
    });
};
window.switchAnalyticsTab = function(tabName) {
    // Hide all panels
    document.querySelectorAll('.analytics-panel').forEach(panel => panel.classList.add('hidden'));
    
    // Reset all buttons to inactive style
    document.querySelectorAll('.analytics-tab').forEach(btn => {
        btn.className = "analytics-tab px-4 py-2 rounded-lg text-sm font-bold bg-white/50 text-brandDark/70 border border-brandDark/10 hover:text-brandDark transition";
    });

    // Show active panel
    const activePanel = document.getElementById(`analytics-panel-${tabName}`);
    if (activePanel) activePanel.classList.remove('hidden');

    // Set active button style
    const activeBtn = document.getElementById(`analytics-tab-${tabName}`);
    if (activeBtn) {
        activeBtn.className = "analytics-tab px-4 py-2 rounded-lg text-sm font-bold bg-brandDark text-white shadow transition";
    }

    // FIX: Force the table to render when the tab is opened
    if (tabName === 'responses') {
        renderAnalyticsResponses();
    }
};

window.renderAnalyticsResponses = function() {
    const container = document.getElementById('analytics-responses-container');
    const filterSelect = document.getElementById('dash-response-campaign-filter');
    const countSpan = document.getElementById('dash-response-count');
    if (!container) return;

    if (filterSelect && filterSelect.options.length <= 1) {
        const campaigns = Object.keys(campaignConfigs);
        campaigns.forEach(c => {
            filterSelect.innerHTML += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`;
        });
    }

    const selectedCampaign = filterSelect ? filterSelect.value : "";
    
    // FIX: Safely check for case-insensitive outcome and campaign keys
    const workedCustomers = mockCustomers.filter(c => {
        const outcome = c.outcome || c.Outcome;
        const campaign = c.campaign || c.Campaign;
        
        if (!outcome) return false; // Skip customers who haven't been called
        if (selectedCampaign && selectedCampaign !== "" && campaign !== selectedCampaign) return false;
        return true;
    });

    if (countSpan) countSpan.innerText = `${workedCustomers.length} updates`;
    
    if (workedCustomers.length === 0) {
        container.innerHTML = '<p class="p-6 text-center text-brandDark/50 italic font-medium">No customer responses match this criteria.</p>';
        return;
    }

    let allKeys = new Set();
    workedCustomers.forEach(c => Object.keys(c).forEach(k => allKeys.add(k)));
    const excludeKeys = ['id', 'pendingReschedule', 'worked'];
    const columns = Array.from(allKeys).filter(k => !excludeKeys.includes(k));

    let tableHTML = `
        <div class="overflow-x-auto">
            <table class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="bg-white/90 sticky top-0 z-10 shadow-sm border-b border-brandDark/20">
                    <tr class="text-[11px] font-semibold uppercase text-brandDark/70">`;
    
    columns.forEach(col => { tableHTML += `<th class="px-4 py-3">${escapeHtml(col)}</th>`; });
    tableHTML += `</tr></thead><tbody class="text-[13px] font-medium">`;

    workedCustomers.forEach(c => {
        tableHTML += `<tr class="border-b border-brandDark/5 hover:bg-white/40 transition">`;
        columns.forEach(col => { tableHTML += `<td class="px-4 py-3">${escapeHtml(c[col] || '--')}</td>`; });
        tableHTML += `</tr>`;
    });

    tableHTML += `</tbody></table></div>`;
    container.innerHTML = tableHTML;
};

window.exportResponsesCSV = function() {
    const filterSelect = document.getElementById('dash-response-campaign-filter');
    const selectedCampaign = filterSelect ? filterSelect.value : "";
    
    const workedCustomers = mockCustomers.filter(c => {
        const outcome = c.outcome || c.Outcome;
        const campaign = c.campaign || c.Campaign;
        if (!outcome) return false;
        if (selectedCampaign && campaign !== selectedCampaign) return false;
        return true;
    });

    if (workedCustomers.length === 0) {
        showAppAlert("No data available to export.", "Export Failed");
        return;
    }

    // Get dynamic headers
    let allKeys = new Set();
    workedCustomers.forEach(c => Object.keys(c).forEach(k => allKeys.add(k)));
    const excludeKeys = ['id', 'pendingReschedule', 'worked'];
    const columns = Array.from(allKeys).filter(k => !excludeKeys.includes(k));

    // Build CSV String
    let csvContent = columns.join(",") + "\n";
    workedCustomers.forEach(c => {
        let row = columns.map(col => {
            let val = c[col] || "";
            val = String(val).replace(/"/g, '""'); // Escape quotes
            return `"${val}"`;
        });
        csvContent += row.join(",") + "\n";
    });

    // Trigger Download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Customer_Responses_${new Date().toISOString().slice(0,10)}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

window.exportResponsesSheets = function() {
    // If you want me to write the Python backend code to push this directly to a new tab in your Google Sheet, let me know! 
    // For now, this triggers the CSV download as a fallback.
    showAppAlert("Exporting as CSV. (Backend Sheets API endpoint required for direct sync).", "Exporting");
    exportResponsesCSV();
};