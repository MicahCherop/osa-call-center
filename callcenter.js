// --- FRONTEND API INTEGRATION ---
const API_BASE = `${window.location.origin}/api`;
let deferredInstallPrompt = null;

function setupPwaInstall() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/service-worker.js').catch(error => {
        console.warn('PWA service worker registration failed:', error);
    });
    window.addEventListener('beforeinstallprompt', event => {
        deferredInstallPrompt = event;
        document.getElementById('pwa-install-button')?.classList.remove('hidden');
    });
    window.addEventListener('appinstalled', () => {
        deferredInstallPrompt = null;
        document.getElementById('pwa-install-button')?.remove();
    });
    if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) return;
    const installButton = document.createElement('button');
    installButton.id = 'pwa-install-button';
    installButton.type = 'button';
    installButton.className = 'pwa-install-button';
    installButton.title = 'Install Call Center Campaigns';
    installButton.setAttribute('aria-label', 'Install Call Center Campaigns');
    installButton.innerHTML = '<i class="fa-solid fa-download"></i><span>Install app</span>';
    installButton.addEventListener('click', async () => {
        if (!deferredInstallPrompt) {
            showAppAlert('Use your browser menu and choose "Install Call Center Campaigns" or "Add to desktop".', 'Install app');
            return;
        }
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        installButton.classList.add('hidden');
    });
    document.body.appendChild(installButton);
}

document.addEventListener('DOMContentLoaded', setupPwaInstall);
const API_CACHE_TTL = 30000;
const API_CACHE_PREFIX = 'CALLCENTER_API_CACHE_';

function apiCacheKey(url) {
    return `${API_CACHE_PREFIX}${url}`;
}

function readApiCache(url) {
    try {
        const cached = JSON.parse(localStorage.getItem(apiCacheKey(url)) || 'null');
        if (!cached || Date.now() - cached.timestamp > API_CACHE_TTL) return null;
        return cached.data;
    } catch {
        return null;
    }
}

function writeApiCache(url, data) {
    try {
        localStorage.setItem(apiCacheKey(url), JSON.stringify({ timestamp: Date.now(), data }));
    } catch (error) {
        console.warn('API cache write skipped:', error);
    }
}

function invalidateApiCache(match) {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key?.startsWith(API_CACHE_PREFIX) && (!match || key.includes(match))) keys.push(key);
    }
    keys.forEach(key => localStorage.removeItem(key));
}

async function cachedApiGet(url, request) {
    const cached = readApiCache(url);
    if (cached !== null) return cached;
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            const data = await request();
            writeApiCache(url, data);
            return data;
        } catch (error) {
            lastError = error;
            if (attempt === 2 || !String(error.message || '').match(/\b(429|500|502|503|504)\b/)) {
                throw error;
            }
            await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 1000));
        }
    }
    throw lastError;
}

// Persistent Local UI States
let activeCustomerId = null;
let activeWorkspaceQueueTab = 'active';
let activeAppModal = null;
let isClockedIn = localStorage.getItem('IS_CLOCKED_IN') === 'true';

// Dynamic Data States
function readCachedArray(key) {
    try {
        const value = JSON.parse(localStorage.getItem(key) || '[]');
        return Array.isArray(value) ? value : [];
    } catch {
        return [];
    }
}

let mockCustomers = readCachedArray('CALLCENTER_CUSTOMERS_CACHE');
let campaignConfigs = {};
let agents = readCachedArray('CALLCENTER_AGENTS_CACHE');
let campaignRecords = readCachedArray('CALLCENTER_CAMPAIGNS_CACHE');
let globalStats = { totalCalls: 0, connected: 0, recovered: 0, outcomes: {} };
let ptpCustomers = [];
let pendingCustomers = [];

function agentName(agent) {
    return String(agent?.name || agent?.Name || '').trim();
}

function agentIsClockedIn(agent) {
    const status = String(agent?.status || agent?.Status || '').trim().toLowerCase();
    return status === 'clocked in' || status === 'online';
}

function agentHasCampaign(agent) {
    const name = agentName(agent);
    return (mockCustomers || []).some(customer => {
        const assignedAgent = String(customer.agentId || customer.AgentId || customer.assignedAgent || '').trim();
        return assignedAgent === name && String(customer.campaign || customer.Campaign || '').trim();
    });
}

function getControlAgentState(agent) {
    if (!agentIsClockedIn(agent)) return 'Offline';
    return agentHasCampaign(agent) ? 'Online (On Call)' : 'Idle';
}

function agentCampaign(agent) {
    const explicitCampaign = agent?.campaign || agent?.Campaign || agent?.currentCampaign;
    if (explicitCampaign) return String(explicitCampaign).trim();
    const name = agentName(agent);
    return (mockCustomers || []).find(customer => {
        const assignedAgent = String(customer.agentId || customer.AgentId || customer.assignedAgent || '').trim();
        return assignedAgent === name && String(customer.campaign || customer.Campaign || '').trim();
    })?.campaign || '';
}

function getAgentQueueCampaign() {
    const campaigns = [...new Set((mockCustomers || [])
        .filter(customer => String(customer.agentId || customer.AgentId || '').trim() === LOGGED_IN_AGENT)
        .map(customer => String(customer.campaign || customer.Campaign || '').trim())
        .filter(Boolean))];
    const storageKey = `ACTIVE_QUEUE_CAMPAIGN_${LOGGED_IN_AGENT || 'unknown'}`;
    const savedCampaign = localStorage.getItem(storageKey);
    const activeCampaign = campaigns.includes(savedCampaign) ? savedCampaign : (campaigns[0] || '');
    if (activeCampaign) localStorage.setItem(storageKey, activeCampaign);
    return activeCampaign;
}

function normalizeCampaignType(value) {
    const type = String(value || '').trim().toLowerCase().replace(/[_-]+/g, ' ');
    if (type === 'active no loan' || type === 'active no loans' || type === 'active with no loan' || type === 'active with no loans') return 'active_no_loan';
    if (type === 'upcoming due' || type === 'upcoming dues') return 'upcoming_dues';
    if (type === 'defaulted' || type === 'defaulters' || type === 'defaulted customers') return 'defaulted';
    if (type === 'dormant') return 'dormant';
    return type.replace(/\s+/g, '_');
}

function customerCampaignType(customer) {
    const campaign = customer?.campaign || customer?.Campaign || '';
    return normalizeCampaignType(campaignConfigs[campaign] || campaign || 'defaulted');
}

function rebuildCampaignConfigs() {
    campaignConfigs = {};
    campaignRecords = campaignRecords.map(normalizeCampaignRecord).filter(campaign => campaign.name);
    campaignRecords.forEach(campaign => {
        campaignConfigs[campaign.name] = campaign.type;
    });

}

function normalizeCampaignRecord(campaign) {
    const value = campaign || {};
    return {
        ...value,
        name: value.name || value.campaignName || value.campaign || value['Campaign Name'] || '',
        type: value.type || value.campaignType || value['Campaign Type'] || '',
        priority: value.priority || value.campaignPriority || value.Priority || '',
        startDate: value.startDate || value.start || value['Start Date'] || '',
        endDate: value.endDate || value.end || value['End Date'] || '',
        accountCount: value.accountCount ?? value.accounts ?? value['Account Count'] ?? 0,
        dateAdded: value.dateAdded || value.createdAt || value['Date Added'] || '',
        archivedAt: value.archivedAt || value.archived_at || ''
    };
}

rebuildCampaignConfigs();

// --- RBAC: SECURITY & ENFORCEMENT ---
const CURRENT_USER_EMAIL = localStorage.getItem('USER_EMAIL');
const CURRENT_USER_ROLE = localStorage.getItem('USER_ROLE');
const CURRENT_USER_NAME = localStorage.getItem('LOGGED_IN_AGENT');
let LOGGED_IN_AGENT = CURRENT_USER_NAME || null; 
const currentPage = document.body.dataset.page;
const isAuthorized = enforceSecurity();

// ==========================================
// 1. STRICT ROLE-BASED ACCESS CONTROL (RBAC)
// ==========================================

function enforceSecurity() {
    const role = localStorage.getItem('USER_ROLE');
    const email = localStorage.getItem('USER_EMAIL');
    const currentPage = document.body.getAttribute('data-page');

    // 1. Unauthenticated users are sent straight to login
    if ((!role || !email) && currentPage !== 'login') {
        window.location.replace('/login');
        return;
    }

    // 2. If already logged in but sitting on the login page, auto-forward them
    if (currentPage === 'login' && role) {
        routeUserByRole(role);
        return;
    }

    // 3. THE ACCESS MATRIX (Define who can see what)
    // Note: These match the 'data-page' attributes on your <body> tags
    const accessMatrix = {
        'Admin': ['overview', 'workspace', 'campaigns', 'teamleader', 'dashboard', 'admin'],
        'Ops Manager': ['overview', 'campaigns', 'teamleader', 'dashboard', 'admin'],
        'Team Leader': ['overview', 'campaigns', 'teamleader', 'dashboard'],
        'Control Agent': ['workspace']
    };

    // 4. Enforce Page Access
    if (currentPage !== 'login') {
        const allowedPages = accessMatrix[role] || [];
        
        // If their role doesn't have the current page in its allowed list:
        if (!allowedPages.includes(currentPage)) {
            routeUserByRole(role); // Boot them to their default page
            return;
        }

        // 5. Hide unauthorized sidebar links visually
        hideUnauthorizedMenuLinks(allowedPages);
    }

    return true;
}

// Helper: Routes users to their specific default dashboard
function routeUserByRole(role) {
    if (['Admin', 'Ops Manager', 'Team Leader'].includes(role)) {
        window.location.replace('/overview');
    } else {
        window.location.replace('/workspace'); 
    }
}

// Helper: Hides sidebar icons the user isn't allowed to click
function hideUnauthorizedMenuLinks(allowedPages) {
    const sidebarLinks = document.querySelectorAll('aside a[data-page]');
    sidebarLinks.forEach(link => {
        const targetPage = link.getAttribute('data-page');
        if (!allowedPages.includes(targetPage)) {
            link.style.display = 'none'; // Erase the button completely
        }
    });
}

// Execute the bouncer immediately when the page loads
document.addEventListener('DOMContentLoaded', enforceSecurity);
window.logout = function() {
    localStorage.clear();
    window.location.replace('/login');
};


window.onload = async () => {
    // Stop execution if they are unauthorized or on the login page
    if (!isAuthorized || currentPage === 'login') return; 
    
    if (!Array.isArray(agents)) agents = [];
    if (!Array.isArray(mockCustomers)) mockCustomers = [];
    initCurrentPage();
    await fetchAllData();
    initCurrentPage();
};
// --- USER PROFILE & LOGOUT LOGIC ---

// Populate the header with the logged-in user's details
function loadUserProfile() {
    const name = localStorage.getItem('LOGGED_IN_AGENT') || 'Unknown User';
    const role = localStorage.getItem('USER_ROLE') || 'Agent';
    const email = localStorage.getItem('USER_EMAIL') || '';

    const nameEl = document.getElementById('header-user-name');
    const roleEl = document.getElementById('header-user-role');
    const emailEl = document.getElementById('modal-user-email');
    const statusIndicator = document.getElementById('global-status-text');

    if (nameEl) nameEl.innerText = name;
    if (roleEl) roleEl.innerText = role;
    if (emailEl) emailEl.innerText = email;

    // Clock controls are available to agents and administrators.
    if (statusIndicator) {
        if (role === 'Control Agent' || role === 'Admin') {
            statusIndicator.style.display = 'flex';
        } else {
            statusIndicator.style.display = 'none';
        }
    }
    ensureHeaderClockControl();
}

function ensureHeaderClockControl() {
    const logoutModal = document.getElementById('logout-modal');
    if (!logoutModal || document.getElementById('header-clock-control')) return;
    const signOutButton = logoutModal.querySelector('button[onclick="logout()"]');
    const control = document.createElement('div');
    control.id = 'header-clock-control';
    control.className = 'px-4 py-3 border-b border-brandDark/5 bg-white';
    control.innerHTML = `<div class="flex items-center justify-between gap-3">
        <span id="header-clock-label" class="text-sm font-medium text-brandDark/70">Clocked Out</span>
        <label class="relative inline-flex items-center cursor-pointer">
          <input type="checkbox" id="header-clock-toggle" class="sr-only peer" onchange="toggleAgentStatus(this)">
          <div class="w-11 h-6 bg-gray-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-500"></div>
        </label>
      </div>`;
    if (signOutButton) logoutModal.insertBefore(control, signOutButton);
    syncHeaderClockControl();
}

function syncHeaderClockControl() {
    const toggle = document.getElementById('header-clock-toggle');
    const label = document.getElementById('header-clock-label');
    if (toggle) toggle.checked = isClockedIn;
    if (label) label.innerText = isClockedIn ? 'Clocked In' : 'Clocked Out';
}

// 2. Toggle the logout dropdown modal
function toggleLogoutModal() {
    const modal = document.getElementById('logout-modal');
    if (modal) {
        modal.classList.toggle('hidden');
    }
}

// 3. Close the modal automatically if the user clicks anywhere else on the screen
document.addEventListener('click', function(event) {
    const modal = document.getElementById('logout-modal');
    const triggerBtn = event.target.closest('button[onclick="toggleLogoutModal()"]');
    
    if (!triggerBtn && modal && !modal.classList.contains('hidden') && !event.target.closest('#logout-modal')) {
        modal.classList.add('hidden');
    }
});

// Run the data loader as soon as the page opens
document.addEventListener('DOMContentLoaded', loadUserProfile);

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
    const listBody = document.getElementById('campaign-list-tbody');
    if (!listBody) return;
    
    if (campaignRecords.length === 0) {
        listBody.innerHTML = '<tr><td colspan="4" class="px-5 py-8 text-center text-brandDark/50 italic">No campaigns found. Create one to get started.</td></tr>';
        return;
    }

    listBody.innerHTML = campaignRecords.map(c => `
        <tr class="border-b border-brandDark/5 hover:bg-white/40 transition cursor-pointer" onclick="openCampaignCustomers(${inlineString(c.name)})" title="View customers in this campaign">
            <td class="px-5 py-4"><span class="inline-flex items-center gap-2 text-brandAmber"><i class="fa-solid fa-bullhorn"></i>${escapeHtml(displayValue(c.type))}</span></td>
            <td class="px-5 py-4"><div class="font-medium">${escapeHtml(displayValue(c.name))}</div><div class="text-xs text-brandDark/50">${escapeHtml(displayValue(c.priority))}</div></td>
            <td class="px-5 py-4 text-right font-medium">${escapeHtml(displayValue(c.accountCount))}</td>
            <td class="px-5 py-4">${escapeHtml(displayValue(c.dateAdded || c.startDate))}</td>
        </tr>
    `).join('');
};

window.openCampaignCustomers = async function(campaignName) {
    const listState = document.getElementById('campaign-list-state');
    const detailsState = document.getElementById('campaign-details-state');
    const title = document.getElementById('campaign-detail-title');
    const tbody = document.getElementById('campaign-customers-tbody');
    if (!listState || !detailsState || !tbody) return;
    listState.classList.add('hidden');
    detailsState.classList.remove('hidden');
    detailsState.classList.add('flex');
    if (title) title.innerText = `${campaignName} Customers`;
    tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-8 text-center text-brandDark/50">Loading customers...</td></tr>';
    try {
        const response = await fetch(`${API_BASE}/customers?campaignName=${encodeURIComponent(campaignName)}&limit=500`);
        if (!response.ok) throw new Error(`Status ${response.status}`);
        const result = await response.json();
        const customers = result.items || [];
        tbody.innerHTML = customers.length ? customers.map(customer => `<tr class="border-b border-brandDark/5 hover:bg-white/40 transition"><td class="px-5 py-4 font-medium">${escapeHtml(customer.name)}</td><td class="px-5 py-4">${escapeHtml(customer.phone || '--')}</td><td class="px-5 py-4">${escapeHtml(customer.sector || '--')}</td><td class="px-5 py-4 text-brandAmber">${escapeHtml(customer.agentId || 'Unassigned')}</td><td class="px-5 py-4">${escapeHtml(customer.outcome || '--')}</td></tr>`).join('') : '<tr><td colspan="5" class="px-5 py-8 text-center text-brandDark/50">No customers in this campaign.</td></tr>';
    } catch (error) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-8 text-center text-red-600">Could not load campaign customers.</td></tr>';
    }
};

window.renderTeamLeaderWorkspace = function() {
    // Optional: Add specific Team Leader UI updates here if needed
    console.log("Team Leader UI updated.");
};

window.renderCampaignAgentSelector = function() {
    // Optional: Add specific Agent Selector UI updates here if needed
};

async function fetchAllData(forceCampaignRefresh = false) {
    try {
        const fetchJson = async (url, bypassCache = false) => {
            const cached = bypassCache ? null : readApiCache(url);
            if (cached !== null) return cached;
            let lastError;
            for (let attempt = 0; attempt < 3; attempt += 1) {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 15000);
                try {
                    const response = await fetch(url, { signal: controller.signal });
                    if (response.ok) {
                        const data = await response.json();
                        writeApiCache(url, data);
                        return data;
                    }
                    lastError = new Error(`API Error: ${response.status}`);
                    if (![429, 500, 502, 503, 504].includes(response.status) || attempt === 2) break;
                } catch (error) {
                    lastError = error;
                    if (attempt === 2) break;
                } finally {
                    clearTimeout(timeout);
                }
                await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 1000));
            }
            throw lastError;
        };

        const customerPages = new Set(['workspace', 'teamleader', 'campaigns', 'overview', 'dashboard', 'admin']);
        const requests = [
            fetchJson(`${API_BASE}/agents`),
            fetchJson(`${API_BASE}/campaigns${forceCampaignRefresh ? '?fresh=1' : ''}`, forceCampaignRefresh),
            customerPages.has(currentPage)
                ? fetchJson(`${API_BASE}/customers?limit=200${currentPage === 'workspace' && LOGGED_IN_AGENT ? `&agentName=${encodeURIComponent(LOGGED_IN_AGENT)}` : ''}`)
                : Promise.resolve({ items: [] })
        ];
        const [agentsResult, campaignsResult, customersResult] = await Promise.allSettled(requests);

        // 1. Fetch Agents
        if (agentsResult.status === 'fulfilled') {
            const agentsData = agentsResult.value;
            window.agents = Array.isArray(agentsData) ? agentsData : (agentsData.agents || agentsData.data || []);
            agents = window.agents;
            localStorage.setItem('CALLCENTER_AGENTS_CACHE', JSON.stringify(agents));
        }
        
        if (campaignsResult.status === 'fulfilled') {
            const campaigns = campaignsResult.value;
            const parsedCampaigns = Array.isArray(campaigns)
                ? campaigns
                : (campaigns.campaigns || campaigns.data || []);
            campaignRecords = parsedCampaigns.map(normalizeCampaignRecord);
            localStorage.setItem('CALLCENTER_CAMPAIGNS_CACHE', JSON.stringify(campaignRecords));
            rebuildCampaignConfigs();
        }

        // 3. Customers are deliberately bounded; load more pages on demand.
        if (customersResult.status === 'fulfilled') {
            const custData = customersResult.value;
            window.customers = Array.isArray(custData) ? custData : (custData.items || custData.customers || custData.data || []);
            mockCustomers = window.customers;
            localStorage.setItem('CALLCENTER_CUSTOMERS_CACHE', JSON.stringify(mockCustomers));
        }

        // 4. Calculate Stats
        recalculateGlobalStats();
        
    } catch (err) {
        console.error("API Error - Could not fetch data:", err);
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
    if (typeof updateCampaignDropdowns === 'function') updateCampaignDropdowns();
    if (typeof updateAnalyticsUI === 'function') updateAnalyticsUI();
    if (typeof renderCampaignList === 'function') renderCampaignList();
    if (typeof renderAgentQueue === 'function') renderAgentQueue();
    if (typeof setActiveNavLink === 'function') setActiveNavLink();

    // ADDED OVERVIEW LOGIC HERE
    if (currentPage === 'overview' && typeof renderOverviewData === 'function') {
        renderOverviewData();
    }

    if (currentPage === 'admin' && typeof renderAdminUserList === 'function') {
        renderAdminUserList();
    }
    if (currentPage === 'teamleader' && typeof renderShiftManager === 'function') {
        renderShiftManager();
        if (typeof renderTLCustomers === 'function') renderTLCustomers();
    }
    if (currentPage === 'workspace' && isClockedIn) {
        restoreClockedInWorkspace();
    }
    syncGlobalClockStatus();
}

function syncGlobalClockStatus() {
    const globalText = document.getElementById('global-status-text');
    if (!globalText || !['Control Agent', 'Admin'].includes(CURRENT_USER_ROLE)) return;
    globalText.innerHTML = isClockedIn
        ? '<span class="w-2 h-2 rounded-full bg-green-500"></span> ONLINE'
        : '<span class="w-2 h-2 rounded-full bg-gray-400"></span> OFFLINE';
    globalText.classList.toggle('text-green-700', isClockedIn);
    globalText.classList.toggle('text-gray-500', !isClockedIn);
}

function restoreClockedInWorkspace() {
    const toggle = document.getElementById('header-clock-toggle');
    if (!toggle || !LOGGED_IN_AGENT) return;
    toggle.checked = true;
    const label = document.getElementById('header-clock-label');
    const idleMsg = document.getElementById('idle-overlay');
    const queuePanel = document.getElementById('workspace-queue');
    const emptyState = document.getElementById('empty-call-state');
    if (label) { label.innerText = 'Clocked In'; label.classList.add('text-green-600'); }
    const workspaceLabel = document.getElementById('clock-status-label');
    if (workspaceLabel) workspaceLabel.innerText = 'Clocked In';
    if (idleMsg) idleMsg.classList.add('hidden');
    if (queuePanel) { queuePanel.classList.remove('hidden'); queuePanel.classList.add('flex'); }
    if (emptyState) { emptyState.classList.remove('hidden'); emptyState.classList.add('flex'); }
    renderAgentQueue();
}

function setActiveNavLink() {
  const page = document.body.dataset.page || 'workspace';
    document.querySelectorAll('aside a[data-page]').forEach(btn => {
    const isActive = btn.dataset.page === page;
    btn.classList.toggle('bg-white/40', isActive);
        btn.classList.toggle('bg-brandAmber/10', isActive);
        btn.classList.toggle('text-brandAmber', isActive);
        btn.classList.toggle('font-medium', isActive);
        btn.classList.toggle('text-brandDark/70', !isActive);
        btn.classList.toggle('border-r-2', isActive);
        btn.classList.toggle('border-brandAmber', isActive);
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
        backdrop.classList.add('flex');
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
        backdrop.classList.remove('flex');
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
    shiftState.classList.add('flex');
    
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
    if (shiftState) shiftState.classList.remove('flex');
    if (detailsState) {
        detailsState.classList.add('hidden');
        detailsState.classList.remove('flex');
    }
  
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
    tabs.forEach(tab => {
        tab.classList.add('hidden');
        tab.classList.remove('flex');
    });

  // Show selected tab content
  const selectedTab = document.getElementById(`tl-tab-${tabName}`);
    if (selectedTab) {
        selectedTab.classList.remove('hidden');
        selectedTab.classList.add('flex');
    }

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
window.parseCSV = function(text) {
    const rows = [];
    let row = [], field = '', quoted = false;
    const source = String(text || '').replace(/^\uFEFF/, '');
    const firstLine = source.split(/\r?\n/, 1)[0] || '';
    const delimiter = (firstLine.match(/\t/g) || []).length > (firstLine.match(/,/g) || []).length ? '\t' : ',';
    for (let index = 0; index < source.length; index += 1) {
        const character = source[index];
        if (character === '"') {
            if (quoted && source[index + 1] === '"') { field += '"'; index += 1; }
            else quoted = !quoted;
        } else if (character === delimiter && !quoted) {
            row.push(field.trim()); field = '';
        } else if ((character === '\n' || character === '\r') && !quoted) {
            if (character === '\r' && source[index + 1] === '\n') index += 1;
            row.push(field.trim());
            if (row.some(value => value !== '')) rows.push(row);
            row = []; field = '';
        } else {
            field += character;
        }
    }
    row.push(field.trim());
    if (row.some(value => value !== '')) rows.push(row);
    if (rows.length < 2) return [];

    const headers = rows[0].map(header => header.trim());
  const results = [];

    for (let i = 1; i < rows.length; i++) {
        const values = rows[i];
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
        // FIX: Changed colspan to 5 to account for the new Actions column
        tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-8 text-center text-brandDark/50 italic">No users found in database.</td></tr>';
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
        
        // FIX: Adjusted padding to px-5 py-4, softened font-bold to font-medium, and appended the Actions column
        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5 hover:bg-white/40 transition">
                <td class="px-5 py-4 font-medium text-brandDark">${escapeHtml(name)}</td>
                <td class="px-5 py-4 text-brandDark/80">${escapeHtml(email)}</td>
                <td class="px-5 py-4"><span class="px-2.5 py-1 rounded-md text-[11px] font-medium ${badgeColor}">${escapeHtml(role)}</span></td>
                <td class="px-5 py-4 font-medium ${status === 'Active' ? 'text-green-600' : 'text-gray-500'}">${escapeHtml(status)}</td>
                <td class="px-5 py-4 text-right">
                    <button onclick="openEditUserModal('${escapeHtml(email)}', '${escapeHtml(name)}', '${escapeHtml(role)}')" class="text-brandDark/40 hover:text-brandAmber transition px-2" title="Edit User">
                        <i class="fa-solid fa-pen"></i>
                    </button>
                    <button onclick="deleteUserAction('${escapeHtml(email)}')" class="text-brandDark/40 hover:text-red-600 transition px-2" title="Delete User">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    });
};

window.switchAdminTab = function(tab) {
    const usersPanel = document.getElementById('admin-users-panel');
    const campaignsPanel = document.getElementById('admin-campaigns-panel');
    if (!usersPanel || !campaignsPanel) return;
    const showUsers = tab === 'users';
    usersPanel.classList.toggle('hidden', !showUsers);
    campaignsPanel.classList.toggle('hidden', showUsers);
    document.getElementById('admin-tab-users').className = showUsers ? 'pb-3 border-b-2 border-brandAmber text-brandAmber text-sm font-medium' : 'pb-3 border-b-2 border-transparent text-brandDark/60 hover:text-brandDark text-sm font-medium';
    document.getElementById('admin-tab-campaigns').className = showUsers ? 'pb-3 border-b-2 border-transparent text-brandDark/60 hover:text-brandDark text-sm font-medium' : 'pb-3 border-b-2 border-brandAmber text-brandAmber text-sm font-medium';
    if (!showUsers) renderAdminCampaignManagement();
};

function renderAdminCampaignManagement() {
    const campaignBody = document.getElementById('admin-campaigns-tbody');
    const campaignFilter = document.getElementById('admin-campaign-filter');
    if (!campaignBody || !campaignFilter) return;
    campaignFilter.innerHTML = '<option value="">Select campaign...</option>' + campaignRecords.map(campaign => `<option value="${escapeHtml(campaign.name)}">${escapeHtml(campaign.name)}</option>`).join('');
    campaignBody.innerHTML = campaignRecords.length ? campaignRecords.map((campaign, index) => `
        <tr class="border-b border-brandDark/5 hover:bg-white/40 transition">
          <td class="px-5 py-4 font-medium">${escapeHtml(campaign.name)}</td><td class="px-5 py-4">${escapeHtml(displayValue(campaign.type))}</td><td class="px-5 py-4">${escapeHtml(displayValue(campaign.priority))}</td><td class="px-5 py-4 text-right">${escapeHtml(displayValue(campaign.accountCount))}</td>
          <td class="px-5 py-4 ${campaign.archivedAt ? 'text-gray-500' : 'text-green-600'}">${campaign.archivedAt ? 'Archived' : 'Active'}</td>
          <td class="px-5 py-4 text-right"><button onclick="editAdminCampaign(${index})" title="Edit campaign" class="text-brandDark/40 hover:text-brandAmber px-2"><i class="fa-solid fa-pen"></i></button>${campaign.archivedAt ? '' : `<button onclick="archiveAdminCampaign(${index})" title="Archive campaign" class="text-brandDark/40 hover:text-red-600 px-2"><i class="fa-solid fa-box-archive"></i></button>`}</td>
        </tr>`).join('') : '<tr><td colspan="6" class="px-5 py-8 text-center text-brandDark/50 italic">No campaigns found.</td></tr>';
}

window.refreshAdminManagement = async function() {
    await fetchAllData(true);
    renderAdminCampaignManagement();
};

window.editAdminCampaign = async function(index) {
    const campaign = campaignRecords[index];
    const name = prompt('Campaign name:', campaign.name);
    if (name === null || !name.trim()) return;
    const type = prompt('Campaign type:', campaign.type);
    if (type === null || !type.trim()) return;
    const priority = prompt('Priority:', campaign.priority);
    if (priority === null || !priority.trim()) return;
    const response = await fetch(`${API_BASE}/admin/campaigns/${encodeURIComponent(campaign.name)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, type, priority, startDate: campaign.startDate, endDate: campaign.endDate, archived: Boolean(campaign.archivedAt), requesterRole: CURRENT_USER_ROLE }) });
    if (!response.ok) return showAppAlert((await response.json()).detail || 'Campaign could not be updated.', 'Update failed');
    await refreshAdminManagement();
};

window.archiveAdminCampaign = async function(index) {
    const campaign = campaignRecords[index];
    if (!confirm(`Archive ${campaign.name}? Its customers and dispositions will be retained.`)) return;
    const response = await fetch(`${API_BASE}/admin/campaigns/${encodeURIComponent(campaign.name)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: campaign.name, type: campaign.type, priority: campaign.priority, startDate: campaign.startDate, endDate: campaign.endDate, archived: true, requesterRole: CURRENT_USER_ROLE }) });
    if (!response.ok) return showAppAlert((await response.json()).detail || 'Campaign could not be archived.', 'Archive failed');
    await refreshAdminManagement();
};

window.loadAdminCampaignRecords = async function() {
    const campaignName = document.getElementById('admin-campaign-filter')?.value;
    const customersBody = document.getElementById('admin-customers-tbody');
    const dispositionsBody = document.getElementById('admin-dispositions-tbody');
    if (!customersBody || !dispositionsBody) return;
    const campaignCustomers = mockCustomers.filter(customer => customer.campaign === campaignName);
    customersBody.innerHTML = campaignCustomers.length ? campaignCustomers.map((customer, index) => `<tr class="border-b border-brandDark/5"><td class="px-4 py-3 font-medium">${escapeHtml(customer.name)}</td><td class="px-4 py-3">${escapeHtml(customer.phone)}</td><td class="px-4 py-3">${escapeHtml(customer.outcome || '--')}</td><td class="px-4 py-3 text-right"><button onclick="editAdminCustomer(${index})" class="text-brandDark/40 hover:text-brandAmber" title="Edit customer"><i class="fa-solid fa-pen"></i></button></td></tr>`).join('') : '<tr><td colspan="4" class="px-4 py-6 text-center text-brandDark/50 italic">Select a campaign with loaded customers.</td></tr>';
    dispositionsBody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-brandDark/50 italic">Loading dispositions...</td></tr>';
    if (!campaignName) { dispositionsBody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-brandDark/50 italic">Select a campaign.</td></tr>'; return; }
    try {
        window.adminDispositions = await (await fetch(`${API_BASE}/admin/dispositions?campaignName=${encodeURIComponent(campaignName)}&requesterRole=${encodeURIComponent(CURRENT_USER_ROLE)}`)).json();
        dispositionsBody.innerHTML = adminDispositions.length ? adminDispositions.map((disposition, index) => `<tr class="border-b border-brandDark/5"><td class="px-4 py-3 font-medium">${escapeHtml(disposition.customer_id)}</td><td class="px-4 py-3">${escapeHtml(disposition.outcome || '--')}</td><td class="px-4 py-3">${escapeHtml(String(disposition.amount_rec ?? 0))}</td><td class="px-4 py-3 text-right"><button onclick="editAdminDisposition(${index})" class="text-brandDark/40 hover:text-brandAmber" title="Edit disposition"><i class="fa-solid fa-pen"></i></button></td></tr>`).join('') : '<tr><td colspan="4" class="px-4 py-6 text-center text-brandDark/50 italic">No dispositions recorded.</td></tr>';
    } catch (error) { dispositionsBody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-red-600">Could not load dispositions.</td></tr>'; }
};

window.editAdminCustomer = async function(index) {
    const campaignName = document.getElementById('admin-campaign-filter')?.value;
    const customer = mockCustomers.filter(item => item.campaign === campaignName)[index];
    const name = prompt('Customer name:', customer.name);
    if (name === null || !name.trim()) return;
    const phone = prompt('Phone:', customer.phone || '');
    if (phone === null) return;
    const outcome = prompt('Outcome:', customer.outcome || '');
    if (outcome === null) return;
    const response = await fetch(`${API_BASE}/admin/customers`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ campaignName, customerId: customer.id, name, phone, branch: customer.branch, sector: customer.sector, balance: customer.balance, outcome, status: customer.status, requesterRole: CURRENT_USER_ROLE }) });
    if (!response.ok) return showAppAlert((await response.json()).detail || 'Customer could not be updated.', 'Update failed');
    await fetchAllData(true);
    loadAdminCampaignRecords();
};

window.editAdminDisposition = async function(index) {
    const disposition = (window.adminDispositions || [])[index];
    if (!disposition) return;
    const outcome = prompt('Outcome:', disposition.outcome || '');
    if (outcome === null || !outcome.trim()) return;
    const amountRec = prompt('Amount recovered:', disposition.amount_rec ?? 0);
    if (amountRec === null || Number.isNaN(Number(amountRec))) return showAppAlert('Enter a valid amount.', 'Invalid amount');
    const comments = prompt('Comments:', disposition.comments || '');
    if (comments === null) return;
    const response = await fetch(`${API_BASE}/admin/dispositions/${encodeURIComponent(disposition.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: disposition.id, outcome, status: disposition.status || '', amountRec: Number(amountRec), comments, businessStatus: disposition.business_status || '', ptpTime: disposition.ptp_time || '', requesterRole: CURRENT_USER_ROLE }) });
    if (!response.ok) return showAppAlert((await response.json()).detail || 'Disposition could not be updated.', 'Update failed');
    loadAdminCampaignRecords();
};
// ==========================================
// USER MANAGEMENT: EDIT & DELETE
// ==========================================

function openEditUserModal(email, name, role) {
    document.getElementById('edit-user-email').value = email;
    document.getElementById('edit-user-name').value = name;
    document.getElementById('edit-user-role').value = role;
    document.getElementById('edit-user-status').value = agents.find(agent => (agent.email || agent.Email) === email)?.status || 'Active';

    const backdrop = document.getElementById('edit-user-modal-backdrop');
    backdrop.classList.remove('hidden');
    setTimeout(() => backdrop.classList.remove('opacity-0'), 10);
}

function closeEditUserModal(e) {
    if (e && e.target !== e.currentTarget) return;
    const backdrop = document.getElementById('edit-user-modal-backdrop');
    backdrop.classList.add('opacity-0');
    setTimeout(() => backdrop.classList.add('hidden'), 300);
}

async function submitEditUser(e) {
    e.preventDefault();
    const email = document.getElementById('edit-user-email').value;
    const name = document.getElementById('edit-user-name').value;
    const role = document.getElementById('edit-user-role').value;
    const status = document.getElementById('edit-user-status').value;

    try {
        const res = await fetch('/api/users/edit', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, name, role, status, requesterRole: CURRENT_USER_ROLE })
        });
        
        if (res.ok) {
            closeEditUserModal();
            location.reload(); // Refresh the page to see changes
        } else {
            const data = await res.json();
            showAppAlert(data.detail || 'Failed to update user.', 'Update failed');
        }
    } catch (err) {
        console.error(err);
        showAppAlert('Error communicating with the server.', 'Network error');
    }
}

async function deleteUserAction(email) {
    if (!confirm(`Are you absolutely sure you want to completely remove ${email} from the system? This action cannot be undone.`)) return;
    
    try {
        const res = await fetch(`/api/users/delete`, { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, requesterRole: CURRENT_USER_ROLE })
        });
        
        if (res.ok) {
            location.reload(); // Refresh the page to see changes
        } else {
             const data = await res.json();
             showAppAlert(data.detail || 'Failed to delete user.', 'Delete failed');
        }
    } catch (err) {
        console.error(err);
        showAppAlert('Error communicating with the server.', 'Network error');
    }
}



window.createUser = async function(event, context) {
    event.preventDefault();
    
    let name, email, role;
    
    // Check if the request is coming from the Admin page or Team Leader page
    if (context === 'admin') {
        name = document.getElementById('new-agent-name').value;
        email = document.getElementById('new-agent-email').value;
        role = document.getElementById('new-agent-role').value;
    } else if (context === 'tl') {
        name = document.getElementById('tl-new-name').value;
        email = document.getElementById('tl-new-email').value;
        role = 'Control Agent'; // TLs can only create Control Agents
    }

    try {
        const res = await fetch('/api/users/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // NO password sent! Just the core identity.
            body: JSON.stringify({ 
                name: name.trim(), 
                email: email.trim().toLowerCase(), 
                role: role,
                status: 'Active',
                requesterRole: CURRENT_USER_ROLE
            })
        });

        if (res.ok) {
            // Refresh the page so the new user appears in the list immediately
            location.reload(); 
        } else {
            const data = await res.json();
            alert(data.detail || "Failed to create user.");
        }
    } catch (error) {
        console.error("Error creating user:", error);
        alert("Error connecting to the server.");
    }
};



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
      
    invalidateApiCache();
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
    const campType = customerCampaignType(activeCustomer);
  
  let dispositionSaved = '';
  let amountRec = 0;
    let ptpTime = document.getElementById('input-ptp-time')?.value || '';
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
    ptpTime: ptpTime,
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
      
    invalidateApiCache();
      await fetchAllData();

      activeCustomerId = null;
      updateWorkspaceStats();
            if (document.getElementById('active-call-panel')) {
                document.getElementById('active-call-panel').classList.add('hidden');
                document.getElementById('active-call-panel').classList.remove('flex');
            }
            if (document.getElementById('empty-call-state')) {
                document.getElementById('empty-call-state').classList.remove('hidden');
                document.getElementById('empty-call-state').classList.add('flex');
            }
      renderAgentQueue();
      
      updateAnalyticsUI();
      renderShiftManager();
      renderTeamLeaderWorkspace();
      
      const campaignsView = document.getElementById('view-campaigns');
      if (campaignsView && !campaignsView.classList.contains('hidden')) {
          renderCampaignList();
      }

      submitBtn.innerHTML = originalText;
      showAppAlert("Disposition saved!", "Success");
  } catch (err) {
      submitBtn.innerHTML = originalText;
      showAppAlert("Failed to save disposition to database.", "Network Error");
  }
}

// ----------------------------------------------------------------------
// CAMPAIGNS & ALLOCATION
// ----------------------------------------------------------------------

function toggleCustomCategory(select) {
    const customInput = document.getElementById('new-campaign-custom-type');
    if (!customInput) return;
    const isCustom = select.value === 'custom';
    customInput.classList.toggle('hidden', !isCustom);
    customInput.required = isCustom;
}

async function submitAddCampaign(e) {
  e.preventDefault();
  const submitBtn = e.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.innerHTML;
  
  const campaignName = document.getElementById('new-campaign-name').value.trim(); 
    const typeSelect = document.getElementById('new-campaign-type');
    const customType = document.getElementById('new-campaign-custom-type');
    const campaignType = typeSelect.value === 'custom' ? customType.value.trim() : typeSelect.value;
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
        if (rawCustomers.length > 20000) {
            showAppAlert("A campaign upload can contain at most 20,000 accounts.", "Upload limit exceeded");
            submitBtn.innerHTML = originalText;
            return;
        }

    // Capture CSV columns dynamically
    const formattedCustomers = rawCustomers.map((row, index) => {
        const customer = { ...row }; 
        const normalizeHeader = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const normalizedRow = Object.fromEntries(Object.entries(row).map(([key, value]) => [normalizeHeader(key), value]));
        const csvValue = (...keys) => keys.map(key => normalizedRow[normalizeHeader(key)]).find(value => value !== undefined && value !== '') || '';
        const parsedId = parseInt(csvValue('id', 'customer_id', 'customer id'), 10);
        customer.id = Number.isFinite(parsedId) ? parsedId : (index + 1);
        customer.name = String(csvValue('name', 'customer', 'customer_name') || "Unknown");
        customer.phone = String(csvValue('phone', 'mobile no', 'mobile_no', 'mobile number') || "");
        customer.branch = String(csvValue('branch', 'station', 'stations') || "Not Specified");
        customer.sector = String(csvValue('sector') || "Not Specified");
        customer.balance = String(csvValue('balance', 'balance today') || "0");
        customer.dueDate = String(csvValue('dueDate', 'due_date', 'Due Date', 'duedate'));
        customer.station = String(csvValue('station', 'Station'));
        customer.stations = String(csvValue('stations', 'Stations'));
        customer.pair = String(csvValue('pair', 'Pair'));
        customer.disbAmount = String(csvValue('disbAmount', 'disb_amount', 'Disb Amount', 'disbursedAmount'));
        customer.totalPaid = String(csvValue('totalPaid', 'total_paid', 'Total Paid'));
        customer.url = String(csvValue('url', 'URL'));
        customer.disbDate = String(csvValue('disbDate', 'disb_date', 'Disb Date'));
        customer.loanCode = String(csvValue('loanCode', 'loan_code', 'Loan Code'));
        customer.ddDays = String(csvValue('ddDays', 'dd_days', 'DD Days'));
        customer.accountStatus = String(csvValue('accountStatus', 'account_status', 'Status'));
        customer.bfcBlc = String(csvValue('bfcBlc', 'bfc_blc', 'BFC/BLC'));
        customer.numberOfLoans = String(csvValue('numberOfLoans', 'number_of_loans', 'No of loans', 'No Of Loans'));
        customer.riskBand = String(csvValue('riskBand', 'risk_band', 'Risk_band', 'Risk Band'));
        customer.incrementStatus = String(csvValue('incrementStatus', 'increment_status', 'Increment', 'Increment Status'));
        customer.affordability = String(csvValue('affordability', 'Affordability'));
        customer.loanLimit = String(csvValue('loanLimit', 'loan_limit', 'Loan Limit'));
        customer.interest = String(csvValue('interest', 'Interest'));
        customer.totalDue = String(csvValue('totalDue', 'total_due', 'Total Due'));
        customer.penalty = String(csvValue('penalty', 'Penalty'));
        customer.daysDormant = String(csvValue('daysDormant', 'days_dormant', 'Days_dorm', 'Days Dormant'));
        customer.daysInactive = String(csvValue('daysInactive', 'days_inactive', 'days_to_s', 'days_since'));
        customer.lastLoanAmount = String(csvValue('lastLoanAmount', 'last_loan_amount', 'Lastloan A', 'Lastloan Amount'));
        customer.campaign = campaignName;
        return customer;
    });

    try {
        const CHUNK_SIZE = 4000; // Five requests are enough for the maximum 20,000-account upload
        
        // Loop through the data and send it in smaller batches
        for (let i = 0; i < formattedCustomers.length; i += CHUNK_SIZE) {
            const chunk = formattedCustomers.slice(i, i + CHUNK_SIZE);
            
            const payload = {
                name: campaignName,
                type: campaignType,
                priority: priority,
                startDate: startDate,
                endDate: endDate,
                chunkIndex: Math.floor(i / CHUNK_SIZE),
                customers: chunk
            };

            let res;
            for (let attempt = 0; attempt < 3; attempt += 1) {
                res = await fetch(`${API_BASE}/campaigns`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (![429, 500, 502, 503, 504].includes(res.status) || attempt === 2) break;
                const retryAfter = Number(res.headers.get('Retry-After')) || (attempt + 1) * 2;
                await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
            }

            if (!res.ok) {
                const errorText = await res.text();
                let errorMessage = `Status ${res.status}`;
                try {
                    const errorData = JSON.parse(errorText);
                    errorMessage = errorData.detail || errorMessage;
                } catch {
                    if (errorText) errorMessage = `${errorMessage}: ${errorText.slice(0, 160)}`;
                }
                throw new Error(errorMessage);
            }
            
            // Update the button text to show progress for massive files
            submitBtn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Uploading ${Math.min(i + CHUNK_SIZE, formattedCustomers.length)} of ${formattedCustomers.length}...`;

            // Small stagger between chunks so the DB never receives back-to-back bulk upserts
            if (i + CHUNK_SIZE < formattedCustomers.length) await new Promise(resolve => setTimeout(resolve, 250));
        }
        
                // Update local state without immediately issuing three more Sheets reads.
                invalidateApiCache();
                if (!campaignRecords.some(campaign => campaign.name === campaignName)) {
                    campaignRecords.push(normalizeCampaignRecord({
                        name: campaignName,
                        type: campaignType,
                        priority,
                        startDate,
                        endDate,
                        accountCount: formattedCustomers.length,
                        dateAdded: new Date().toISOString().slice(0, 10)
                    }));
                }
                mockCustomers = [...mockCustomers, ...formattedCustomers];
                localStorage.setItem('CALLCENTER_CAMPAIGNS_CACHE', JSON.stringify(campaignRecords));
                localStorage.setItem('CALLCENTER_CUSTOMERS_CACHE', JSON.stringify(mockCustomers));
                rebuildCampaignConfigs();
        updateCampaignDropdowns();
        renderCampaignList();
        await fetchAllData(true);
        renderCampaignList();
        
        submitBtn.innerHTML = originalText;
        closeAddCampaignModal();
        e.target.reset();
        if (document.getElementById('csv-filename')) {
          document.getElementById('csv-filename').innerText = "No file selected";
        }
        
        showAppAlert(`Success! Created campaign "${campaignName}" and imported ${formattedCustomers.length} customers.`, "Campaign created");
    } catch (err) {
        submitBtn.innerHTML = originalText;
        console.error("Campaign Creation Error:", err);
        showAppAlert(err.message || "Failed to create campaign or upload all customers.", "Upload Error");
    }
  };
  
  // Trigger the file reader
  reader.readAsText(file);
}

async function distributeCustomers() {
    if (!['Admin', 'Ops Manager', 'Team Leader'].includes(CURRENT_USER_ROLE)) {
        showAppAlert('Only managers can assign accounts.', 'Permission Denied');
        return;
    }
    const campEl = document.getElementById('allocate-campaign');
  const campaign = campEl ? campEl.value : '';
  if (!campaign) {
    showAppAlert("Please select a campaign from the dropdown first.", "Campaign required");
    return;
  }
  
  try {
      const res = await fetch(`${API_BASE}/distribute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
              campaign,
              selectedAgents: Array.from(document.querySelectorAll('input[name="selected-agents"]:checked')).map(box => box.value),
              requesterRole: CURRENT_USER_ROLE
          })
      });
      const data = await res.json();
    invalidateApiCache();
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
        const role = String(a.Role || a.role || '').trim().toLowerCase();
        return role === 'control agent' && agentIsClockedIn(a) && !agentHasCampaign(a);
    });

    if (controlAgents.length === 0) {
        agentsContainer.innerHTML = '<p class="text-[13px] text-red-500 font-bold p-3">No Active Control Agents available.</p>';
        return;
    }

    // Build a scrollable list of checkboxes
    let html = '<div class="max-h-40 overflow-y-auto p-2 space-y-1">';
    controlAgents.forEach(a => {
        const name = a.Name || a.name;
        html += `
            <label class="flex items-center gap-3 p-2 hover:bg-white/80 rounded-md cursor-pointer transition">
                <input type="checkbox" name="selected-agents" value="${escapeHtml(name)}" onchange="syncSelectAllAgents()" class="w-4 h-4 text-brandAmber rounded border-brandDark/20 focus:ring-brandAmber">
                <span class="text-[13px] font-bold text-brandDark">${escapeHtml(name)}</span>
            </label>
        `;
    });
    html += '</div>';

    agentsContainer.innerHTML = html;
    const selectAll = document.getElementById('select-all-agents');
    if (selectAll) selectAll.checked = false;
};

window.toggleSelectAllAgents = function(selectAll) {
    document.querySelectorAll('input[name="selected-agents"]').forEach(box => {
        box.checked = selectAll.checked;
    });
};

window.syncSelectAllAgents = function() {
    const boxes = Array.from(document.querySelectorAll('input[name="selected-agents"]'));
    const selectAll = document.getElementById('select-all-agents');
    if (selectAll) selectAll.checked = boxes.length > 0 && boxes.every(box => box.checked);
};

// 2. Handle the form submission and distribute customers
window.submitAllocation = function(e) {
    e.preventDefault();
    if (!['Admin', 'Ops Manager', 'Team Leader'].includes(CURRENT_USER_ROLE)) {
        showAppAlert('Only managers can assign accounts.', 'Permission Denied');
        return;
    }
    
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

    fetch(`${API_BASE}/distribute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, selectedAgents, requesterRole: CURRENT_USER_ROLE })
    }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Allocation failed');
        invalidateApiCache();
        await fetchAllData();
        renderShiftManager();
        updateAvailableAgents();
        showAppAlert(data.message || `Successfully distributed ${data.assignedCount} customers.`, "Allocation Complete");
    }).catch(error => showAppAlert(error.message, "Allocation Failed"));
    
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

function inlineString(value) {
    return `'${String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\r?\n/g, '\\n')}'`;
}

function formatMoney(value) {
  const numericValue = Number(String(value ?? 0).replace(/[^\d.-]/g, '')) || 0;
  return `Sh ${numericValue.toLocaleString()}`;
}

function campaignTypeLabel(type) {
        return ({ active_no_loan: 'Active With No Loans', upcoming_dues: 'Upcoming Dues', defaulted: 'Defaulted', dormant: 'Dormant' })[normalizeCampaignType(type)] || String(type || 'Campaign');
}

function activeCampaignRecord(name) {
        return campaignRecords.find(campaign => campaign.name === name);
}

function updateWorkspaceQueueControls(campaignName) {
        const campaign = activeCampaignRecord(campaignName);
        const supportsPtp = ['upcoming_dues', 'defaulted'].includes(normalizeCampaignType(campaign?.type));
        const takeAccountButton = document.getElementById('take-account-button');
        const ptpButton = document.getElementById('ws-queue-tab-ptp');
        const tabs = document.getElementById('workspace-queue-tabs');
        if (takeAccountButton) takeAccountButton.classList.toggle('hidden', CURRENT_USER_ROLE !== 'Admin');
        if (ptpButton) ptpButton.classList.toggle('hidden', !supportsPtp);
        if (tabs) tabs.classList.toggle('grid-cols-3', supportsPtp);
        if (!supportsPtp && activeWorkspaceQueueTab === 'ptp') activeWorkspaceQueueTab = 'active';
}

function switchWorkspaceQueueTab(tab) {
  activeWorkspaceQueueTab = tab;
    ['active', 'pending', 'ptp'].forEach(tabName => {
    const isActive = tabName === tab;
    const btn = document.getElementById(`ws-queue-tab-${tabName}`);
    if (!btn) return;
    btn.classList.toggle('bg-brandAmber', isActive);
    btn.classList.toggle('text-white', isActive);
    btn.classList.toggle('bg-brandAmber/10', !isActive);
    btn.classList.toggle('text-amber-700', !isActive);
    btn.classList.toggle('border', !isActive);
    btn.classList.toggle('border-brandDark/10', !isActive);
  });
  renderAgentQueue();
}

async function toggleAgentStatus(checkbox) {
  isClockedIn = checkbox.checked;
    localStorage.setItem('IS_CLOCKED_IN', String(isClockedIn));
    syncHeaderClockControl();
        const status = isClockedIn ? 'Online' : 'Clocked Out';
        const currentAgent = agents.find(agent => agentName(agent) === LOGGED_IN_AGENT);
        if (currentAgent) currentAgent.status = status;
        try {
            const response = await fetch(`${API_BASE}/agents/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: LOGGED_IN_AGENT, status })
            });
            if (!response.ok) throw new Error(`Status ${response.status}`);
            invalidateApiCache();
        } catch (error) {
            console.error('Failed to persist agent status:', error);
            showAppAlert('Your clock status could not be saved. Please try again.', 'Status update failed');
        }
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
    if (queuePanel) { queuePanel.classList.remove('hidden'); queuePanel.classList.add('flex'); }
    
    if (!activeCustomerId && emptyState) { emptyState.classList.remove('hidden'); emptyState.classList.add('flex'); }
    renderAgentQueue();
  } else {
    if (label) { label.innerText = "Clocked Out"; label.classList.remove('text-green-600'); }
    if (globalText) {
      globalText.classList.replace('text-green-700', 'text-gray-500');
      globalText.innerHTML = `<span class="w-2 h-2 rounded-full bg-gray-400"></span> OFFLINE`;
    }
    if (idleMsg) idleMsg.classList.remove('hidden');
    if (queuePanel) { queuePanel.classList.add('hidden'); queuePanel.classList.remove('flex'); }
    if (emptyState) { emptyState.classList.add('hidden'); emptyState.classList.remove('flex'); }
    if (activeCall) { activeCall.classList.add('hidden'); activeCall.classList.remove('flex'); }
  }
        syncGlobalClockStatus();
}

// 1. OPTIMIZED AGENT QUEUE (Fixes Workspace Freeze)
async function renderAgentQueue() {
    const queueDiv = document.getElementById('agent-customer-list');
    if (!queueDiv || !LOGGED_IN_AGENT) return;
    const countSpan = document.getElementById('queue-count');
        const activeCampaign = getAgentQueueCampaign();
    const campaignLabel = document.getElementById('active-queue-campaign');
    const campaign = activeCampaignRecord(activeCampaign);
    if (campaignLabel) campaignLabel.innerText = activeCampaign ? `${activeCampaign} - ${campaignTypeLabel(campaign?.type)}` : '';
    updateWorkspaceQueueControls(activeCampaign);

    if (activeWorkspaceQueueTab === 'ptp') {
        try {
            const response = await fetch(`${API_BASE}/ptps?agentName=${encodeURIComponent(LOGGED_IN_AGENT)}&campaignName=${encodeURIComponent(activeCampaign)}`);
            if (!response.ok) throw new Error(`Status ${response.status}`);
            ptpCustomers = await response.json();
        } catch (error) {
            queueDiv.innerHTML = '<div class="p-4 text-center text-red-600 text-sm font-medium">Could not load PTP records.</div>';
            return;
        }
    }
    if (activeWorkspaceQueueTab === 'pending') {
        try {
            const response = await fetch(`${API_BASE}/customers?agentName=${encodeURIComponent(LOGGED_IN_AGENT)}&campaignName=${encodeURIComponent(activeCampaign)}&pending=true&limit=500`);
            if (!response.ok) throw new Error(`Status ${response.status}`);
            pendingCustomers = (await response.json()).items || [];
        } catch (error) {
            queueDiv.innerHTML = '<div class="p-4 text-center text-red-600 text-sm font-medium">Could not load pending callbacks.</div>';
            return;
        }
    }

    const myCustomers = activeWorkspaceQueueTab === 'ptp' ? ptpCustomers : activeWorkspaceQueueTab === 'pending' ? pendingCustomers : mockCustomers.filter(c => {
            const customerCampaign = String(c.campaign || c.Campaign || '').trim();
            if (customerCampaign !== activeCampaign) return false;
      return c.agentId === LOGGED_IN_AGENT && String(c.worked).toUpperCase() !== 'TRUE' && !c.pendingReschedule;
    });

    if (countSpan) {
            countSpan.innerText = activeWorkspaceQueueTab === 'pending'
        ? `${myCustomers.length} Pending`
                : activeWorkspaceQueueTab === 'ptp' ? `${myCustomers.length} PTP`
        : `${myCustomers.length} Remaining`;
    }

    if (myCustomers.length === 0) {
        queueDiv.innerHTML = `<div class="p-4 text-center text-brandDark/50 text-sm font-medium">${activeWorkspaceQueueTab === 'pending' ? 'No pending callbacks.' : activeWorkspaceQueueTab === 'ptp' ? 'No PTPs recorded for this campaign.' : 'Your queue is empty.'}</div>`;
        return;
    }

    // FAST RENDER: Build string first, only render top 100 to save memory
    let htmlString = '';
    myCustomers.slice(0, 100).forEach(c => {
        htmlString += `
        <div class="bg-white/80 border border-white hover:border-brandAmber/50 hover:shadow-md p-3 rounded-lg ${activeWorkspaceQueueTab === 'ptp' ? '' : 'cursor-pointer'} transition flex flex-col gap-1" ${activeWorkspaceQueueTab === 'ptp' ? '' : `onclick="startCall(${inlineString(c.id)})"`}>
            <div class="flex justify-between items-center">
            <button type="button" onclick="event.stopPropagation(); openCustomerDrawer(${inlineString(c.id)})" class="font-bold text-sm text-brandDark text-left hover:text-brandAmber transition">${escapeHtml(c.name)}</button>
                <i class="fa-solid fa-phone text-brandAmber text-xs"></i>
            </div>
            <div class="text-xs text-brandDark/60">${escapeHtml(c.campaign || '')}</div>
            ${c.pendingReschedule ? '<div class="text-[11px] font-bold text-amber-700">Pending callback</div>' : ''}
            ${activeWorkspaceQueueTab === 'ptp' ? `<div class="text-[11px] font-bold text-green-700">PTP: ${escapeHtml(c.ptpTime || 'Time not set')}</div>` : ''}
        </div>`;
    });
    
    // Inject once
    queueDiv.innerHTML = htmlString;
}


// 2. OPTIMIZED TEAM LEADER PENDING (Fixes Team Leader Freeze)
window.renderTLPending = function() {
    const tbody = document.getElementById('tl-pending-tbody');
    if (!tbody) return;

    const pending = window.customers.filter(c => {
        const worked = String(c.Worked || c.worked || 'FALSE').toUpperCase();
        return worked !== 'TRUE';
    });
    const columns = customerColumns(pending[0]);
    const table = tbody.closest('table');
    const thead = table?.querySelector('thead');
    if (thead) {
        thead.innerHTML = `<tr class="text-[11px] font-semibold uppercase tracking-wider text-brandDark/50">${columns.map(([, label]) => `<th class="px-5 py-4">${escapeHtml(label)}</th>`).join('')}</tr>`;
    }

    if (pending.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${columns.length}" class="px-5 py-4 text-center text-brandDark/50">No pending callbacks.</td></tr>`;
        return;
    }

    // FAST RENDER: Build string first, only render top 200
    let htmlString = '';
    pending.slice(0, 200).forEach(c => {
        htmlString += `
            <tr class="border-b border-brandDark/5 hover:bg-slate-50/50 transition">
                ${columns.map(([key]) => `<td class="px-5 py-4 ${key === 'agentId' ? 'font-medium text-brandAmber' : 'text-brandDark/70'}">${escapeHtml(displayValue(c[key]))}</td>`).join('')}
            </tr>
        `;
    });
    
    tbody.innerHTML = htmlString;
};


// 3. OPTIMIZED TEAM LEADER CUSTOMERS (Fixes All Customers Freeze)
window.renderTLCustomers = function() {
    const tbody = document.getElementById('tl-customers-tbody');
    if (!tbody) return;
    
    const table = tbody.closest('table');

    if (!mockCustomers || mockCustomers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${CUSTOMER_DISPLAY_COLUMNS.length}" class="px-5 py-4 text-center text-brandDark/50">No customers found in the database.</td></tr>`;
        return;
    }

    const selectedCampaign = document.getElementById('tl-campaign-filter')?.value || 'ALL';
    const campaignFilter = document.getElementById('tl-campaign-filter');
    if (campaignFilter && campaignFilter.options.length <= 1) {
        Object.keys(campaignConfigs).forEach(campaign => {
            campaignFilter.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(campaign)}">${escapeHtml(campaign)}</option>`);
        });
    }
    const visibleCustomers = selectedCampaign === 'ALL'
        ? mockCustomers
        : mockCustomers.filter(customer => customer.campaign === selectedCampaign);
    const columns = selectedCampaign === 'ALL'
        ? CUSTOMER_DISPLAY_COLUMNS
        : customerColumns(visibleCustomers[0]);
    let theadHTML = `<tr class="text-[11px] font-semibold uppercase text-brandDark/70 bg-white/90 sticky top-0 border-b border-brandDark/10">`;
    columns.forEach(([, label]) => {
        theadHTML += `<th class="px-5 py-3 text-left whitespace-nowrap">${escapeHtml(label)}</th>`;
    });
    theadHTML += `</tr>`;

    // FAST RENDER: Only render the first 200 items in the DOM
    let tbodyHTML = '';
    visibleCustomers.slice(0, 200).forEach(c => {
        tbodyHTML += `<tr class="border-b border-brandDark/5 hover:bg-slate-50/50 transition">`;
        columns.forEach(([key]) => {
            const colorClass = key === 'agentId' ? 'font-medium text-brandAmber' : 'text-brandDark/80';
            tbodyHTML += `<td class="px-5 py-4 whitespace-nowrap text-sm ${colorClass}">${escapeHtml(displayValue(c[key]))}</td>`;
        });
        tbodyHTML += `</tr>`;
    });

    if (table) {
        let thead = table.querySelector('thead');
        if (!thead) {
            thead = document.createElement('thead');
            table.insertBefore(thead, table.firstChild);
        }
        thead.innerHTML = theadHTML;
    }
    tbody.innerHTML = tbodyHTML;
};

function startCall(id) {
  activeCustomerId = id;
  const c = mockCustomers.find(x => x.id === id);
  if (!c) return;

    if (document.getElementById('empty-call-state')) {
        document.getElementById('empty-call-state').classList.add('hidden');
        document.getElementById('empty-call-state').classList.remove('flex');
    }
    if (document.getElementById('active-call-panel')) {
        document.getElementById('active-call-panel').classList.remove('hidden');
        document.getElementById('active-call-panel').classList.add('flex');
    }

  if (document.getElementById('active-name')) document.getElementById('active-name').innerText = c.name || 'Unknown';
  if (document.getElementById('active-phone')) document.getElementById('active-phone').innerText = c.phone || '--';
  if (document.getElementById('active-campaign')) document.getElementById('active-campaign').innerText = c.campaign || '--';
  if (document.getElementById('active-debt')) document.getElementById('active-debt').innerText = c.balance || '0';
  if (document.getElementById('active-branch')) document.getElementById('active-branch').innerText = `${c.branch || '--'} / ${c.sector || '--'}`;
    const sourceData = c.sourceData || {};
    const sourceValue = (...headers) => {
        const aliases = headers.map(header => String(header).toLowerCase().replace(/[^a-z0-9]/g, ''));
        const match = Object.entries(sourceData).find(([key, value]) => aliases.includes(String(key).toLowerCase().replace(/[^a-z0-9]/g, '')) && value !== '');
        return match ? match[1] : '';
    };
    const detailValues = {
        'active-mobile-no': c.phone,
        'active-disb-date': sourceValue('disb date', 'loan date', 'current_loan_dormancy_date'),
        'active-due-date': c.dueDate || sourceValue('loan due', 'due date'),
        'active-stations': c.branch || sourceValue('stations', 'station'),
        'active-pair': c.pair,
        'active-sector': c.sector,
        'active-loan-code': sourceValue('loan code'),
        'active-dd-days': sourceValue('dd days'),
        'active-status': sourceValue('account status', 'status'),
        'active-loyalty': c.loyalty || sourceValue('loyalty'),
        'active-no-of-loans': sourceValue('no of loans', 'loan num'),
        'active-risk-band': sourceValue('risk band'),
        'active-increment-status': sourceValue('increment status', 'increment'),
        'active-affordability': sourceValue('affordability'),
        'active-loan-limit': sourceValue('loan limit'),
        'active-disb-amount': c.disbAmount || sourceValue('disb amount'),
        'active-interest': sourceValue('interest'),
        'active-total-due': sourceValue('total due'),
        'active-penalty': sourceValue('penalty'),
        'active-total-paid': c.totalPaid || sourceValue('total paid'),
        'active-balance-today': c.balance || sourceValue('balance today')
    };
    Object.entries(detailValues).forEach(([elementId, value]) => {
        const element = document.getElementById(elementId);
        if (element) element.innerText = value || '--';
    });
    const sourceUrl = sourceValue('url', 'shujaa url', 'merlin url');
    const urlElement = document.getElementById('active-url');
    if (urlElement) {
        urlElement.innerText = sourceUrl || '--';
        urlElement.href = sourceUrl && /^https?:\/\//i.test(sourceUrl) ? sourceUrl : '';
    }
  
  if (!c.agentId) c.agentId = LOGGED_IN_AGENT;
  c.pendingReschedule = false;
  
  const form = document.getElementById('disposition-form');
  if (form) form.reset();
  
  const resContainer = document.getElementById('container-customer-response');
  const statContainer = document.getElementById('container-account-status');

  handleOutcomeChangeGlass(); 
}

function handleOutcomeChangeGlass() {
  const statusEl = document.getElementById('disp-status');
  const outcomeEl = document.getElementById('disp-outcome');
  const amtContainer = document.getElementById('dynamic-amount');
  const amtInput = document.getElementById('input-amount');
  const responseInput = document.getElementById('disp-response');
    const responseContainer = document.getElementById('container-customer-response');
    const businessContainer = document.getElementById('container-business-status');
    const accountContainer = document.getElementById('container-account-status');
    const ptpTimeInput = document.getElementById('input-ptp-time');
  const activeCustomer = mockCustomers.find(x => x.id === activeCustomerId);
    const campType = customerCampaignType(activeCustomer);

  const status = statusEl ? statusEl.value : '';
  const outcome = outcomeEl ? outcomeEl.value : '';

    const isAnswered = outcome === 'Answered';
    const isCustomerResponseCampaign = campType === 'active_no_loan' || campType === 'dormant';
    const isPtpCampaign = campType === 'defaulted' || campType === 'upcoming_dues';
    responseContainer?.classList.toggle('hidden', !isAnswered || !isCustomerResponseCampaign);
    businessContainer?.classList.toggle('hidden', !isAnswered);
    accountContainer?.classList.toggle('hidden', !isAnswered || !isPtpCampaign);
    if (responseInput) responseInput.required = isAnswered && isCustomerResponseCampaign;
    if (statusEl) statusEl.required = isAnswered && isPtpCampaign;
  
  if (amtContainer && amtInput) {
    const needsPtpDetails = isAnswered && isPtpCampaign && status === 'Promise to Pay (PTP)';
    if (needsPtpDetails) {
      amtContainer.classList.remove('hidden');
            amtContainer.classList.add('flex');
      amtInput.required = true;
            if (ptpTimeInput) ptpTimeInput.required = true;
    } else {
      amtContainer.classList.add('hidden');
            amtContainer.classList.remove('flex');
      amtInput.required = false;
    if (ptpTimeInput) ptpTimeInput.required = false;
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

    const columns = CUSTOMER_DISPLAY_COLUMNS
        .filter(([key]) => key !== 'id' && key !== 'worked')
        .map(([key]) => key);

    let tableHTML = `
        <div class="overflow-x-auto">
            <table class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="bg-white/90 sticky top-0 z-10 shadow-sm border-b border-brandDark/20">
                    <tr class="text-[11px] font-semibold uppercase text-brandDark/70">`;
    
    columns.forEach(col => {
        const label = CUSTOMER_DISPLAY_COLUMNS.find(([key]) => key === col)[1];
        tableHTML += `<th class="px-4 py-3">${escapeHtml(label)}</th>`;
    });
    tableHTML += `</tr></thead><tbody class="text-[13px] font-medium">`;

    workedCustomers.forEach(c => {
        tableHTML += `<tr class="border-b border-brandDark/5 hover:bg-white/40 transition">`;
        columns.forEach(col => { tableHTML += `<td class="px-4 py-3">${escapeHtml(displayValue(c[col]))}</td>`; });
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
    const columns = CUSTOMER_DISPLAY_COLUMNS
        .filter(([key]) => key !== 'id' && key !== 'worked')
        .map(([key]) => key);

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

// ==========================================
// DATA FETCHING & INITIALIZATION
// ==========================================

// Global variables to store the data
window.agents = [];
window.customers = [];

// 1. Fetch Agents (Used by Admin and Team Leader pages)
window.fetchAgentsData = async function() {
    try {
        const url = `${API_BASE}/agents`;
        const data = await cachedApiGet(url, async () => {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Status ${res.status}`);
            return res.json();
        });
        if (data) {
            window.agents = Array.isArray(data) ? data : (data.agents || data.data || []);
            agents = window.agents;
            
            const currentPage = document.body.getAttribute('data-page');
            
            // If on Admin page, render the Admin table
            if (currentPage === 'admin' && typeof renderAdminUserList === 'function') {
                renderAdminUserList();
            }
            
            // If on Team Leader page, render the Shift Roster
            if (currentPage === 'teamleader' && typeof renderShiftManager === 'function') {
                renderShiftManager();
            }
        }
    } catch (err) {
        console.error("Failed to fetch agents:", err);
    }
};

// 2. Fetch Customers (Used by Team Leader page)
window.fetchCustomersData = async function() {
    try {
        const url = `${API_BASE}/customers?limit=200`;
        const data = await cachedApiGet(url, async () => {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Status ${res.status}`);
            return res.json();
        });
        if (data) {
            window.customers = Array.isArray(data) ? data : (data.items || data.customers || data.data || []);
            mockCustomers = window.customers;
            
            const currentPage = document.body.getAttribute('data-page');
            
            if (currentPage === 'teamleader') {
                if (typeof renderTLCustomers === 'function') renderTLCustomers();
                if (typeof renderTLPending === 'function') renderTLPending();
            }
        }
    } catch (err) {
        console.error("Failed to fetch customers:", err);
    }
};

// 3. Initialize everything when the page loads
document.addEventListener('DOMContentLoaded', () => {
    const currentPage = document.body.getAttribute('data-page');
    
    // Always load the header profile (name/role)
    if (typeof loadUserProfile === 'function') loadUserProfile();

    // Fetch the necessary data depending on which page we are on
    if (currentPage === 'admin') {
        fetchAgentsData();
    } 
    else if (currentPage === 'teamleader') {
        fetchAgentsData();
        fetchCustomersData();
    }
    else if (currentPage === 'workspace') {
        // Assuming your workspace has its own fetch function for assigned leads
        if (typeof fetchWorkspaceCustomers === 'function') fetchWorkspaceCustomers();
    }
});
// ==========================================
// DATA RENDERING & OVERVIEW LOGIC
// ==========================================

window.renderShiftManager = function() {
    const tbody = document.getElementById('shift-manager-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const visibleAgents = (agents || []).filter(agent => {
        const role = String(agent.role || agent.Role || '').trim().toLowerCase();
        return role === 'control agent' || role === 'admin';
    });

    if (visibleAgents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-4 text-center text-brandDark/50">No agents found.</td></tr>';
        return;
    }

    visibleAgents.forEach(a => {
        const name = a.name || a.Name || 'Unknown';
        const role = a.role || a.Role || '--';
        const status = role.toLowerCase() === 'control agent' ? getControlAgentState(a) : 'Offline';
        const campaign = agentCampaign(a) || 'None';
        
        let statusColor = status === 'Online (On Call)' ? 'text-green-500' : status === 'Idle' ? 'text-amber-500' : 'text-gray-400';

        tbody.innerHTML += `
            <tr class="border-b border-brandDark/5 hover:bg-slate-50/50 transition">
                <td class="px-5 py-4 font-medium">${escapeHtml(name)}</td>
                <td class="px-5 py-4 text-brandDark/70">${escapeHtml(role)}</td>
                <td class="px-5 py-4 font-medium ${statusColor}"><i class="fa-solid fa-circle text-[8px] mr-2"></i>${escapeHtml(status)}</td>
                <td class="px-5 py-4 text-brandDark/70">${escapeHtml(campaign)}</td>
                <td class="px-5 py-4 text-brandDark/70">--</td>
            </tr>
        `;
    });
};


window.renderOverviewData = function() {
    if (currentPage !== 'overview') return;

    // Pulse: Count active agents
    const controlAgents = (agents || []).filter(a => {
        const role = String(a.Role || a.role || '').trim().toLowerCase();
        return role === 'control agent';
    });
    const onCallAgents = controlAgents.filter(agent => getControlAgentState(agent) === 'Online (On Call)');
    const onlineEl = document.getElementById('ov-online-count');
    if (onlineEl) onlineEl.innerText = onCallAgents.length;

    const idleEl = document.getElementById('ov-idle-pulse-count');
    const offlineEl = document.getElementById('ov-offline-count');
    const idleAgents = controlAgents.filter(agent => getControlAgentState(agent) === 'Idle');
    if (idleEl) idleEl.innerText = idleAgents.length;
    if (offlineEl) offlineEl.innerText = Math.max(controlAgents.length - onCallAgents.length - idleAgents.length, 0);

    // Bottlenecks: Count unassigned leads
    const unassigned = (mockCustomers || []).filter(c => {
        const worked = String(c.worked || c.Worked || 'FALSE').toUpperCase();
        const agent = c.agentId || c.AgentId || '';
        return worked !== 'TRUE' && (!agent || agent.trim() === '');
    });
    
    const unassignedEl = document.getElementById('ov-unassigned-count');
    if (unassignedEl) unassignedEl.innerText = unassigned.length;

    let interestedTotal = 0;
    let defaultedCollected = 0;
    let upcomingCollected = 0;
    (mockCustomers || []).forEach(customer => {
        const response = String(customer.status || customer.response || customer.feedback || '').trim().toLowerCase();
        const campaignType = customerCampaignType(customer);
        if (response === 'interested') interestedTotal += 1;
        const amount = Number(String(customer.amountRec ?? customer.ptpAmount ?? customer.amountRecovered ?? customer.conversion ?? 0).replace(/[^\d.-]/g, '')) || 0;
        if (campaignType === 'defaulted') defaultedCollected += amount;
        if (campaignType === 'upcoming_dues') upcomingCollected += amount;
    });
    const interestedEl = document.getElementById('ov-interested-total');
    const defaultedEl = document.getElementById('ov-defaulted-collected');
    const upcomingEl = document.getElementById('ov-upcoming-collected');
    if (interestedEl) interestedEl.innerText = interestedTotal.toLocaleString();
    if (defaultedEl) defaultedEl.innerText = `Sh ${defaultedCollected.toLocaleString()}`;
    if (upcomingEl) upcomingEl.innerText = `Sh ${upcomingCollected.toLocaleString()}`;

    const productivityBody = document.getElementById('ov-agent-productivity');
    if (productivityBody) {
        if (controlAgents.length === 0) {
            productivityBody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-brandDark/50 italic">No agents found.</td></tr>';
            return;
        }
        productivityBody.innerHTML = controlAgents.map(agent => {
            const name = agent.name || agent.Name || 'Unknown';
            const role = agent.role || agent.Role || '--';
            const status = getControlAgentState(agent);
            const calls = Number(agent.callsMade || agent.CallsMade || 0);
            const connected = Number(agent.connected || agent.Connected || 0);
            const recovered = Number(agent.conversion || agent.Conversion || 0);
            const rate = calls ? Math.round((connected / calls) * 100) : 0;
            const statusClass = status === 'Online (On Call)' ? 'text-green-600' : status === 'Idle' ? 'text-amber-600' : 'text-brandDark/50';
            return `<tr class="border-b border-brandDark/5 hover:bg-white/40 transition">
                <td class="px-6 py-4 font-medium">${escapeHtml(name)}</td>
                <td class="px-6 py-4">${escapeHtml(role)}</td>
                <td class="px-6 py-4 font-medium ${statusClass}">${escapeHtml(status)}</td>
                <td class="px-6 py-4 text-right">${calls}</td>
                <td class="px-6 py-4 text-right">${connected}</td>
                <td class="px-6 py-4 text-right">${rate}%</td>
                <td class="px-6 py-4 text-right font-medium">${recovered.toLocaleString()}</td>
            </tr>`;
        }).join('');
    }

    const campaignsBody = document.getElementById('ov-campaigns-list');
    if (campaignsBody) {
        const campaignNames = campaignRecords
            .map(campaign => normalizeCampaignRecord(campaign).name)
            .filter(Boolean);
        const campaignProgress = campaignNames.map(name => {
            const customers = (mockCustomers || []).filter(customer => String(customer.campaign || '').trim() === name);
            const completed = customers.filter(customer => String(customer.worked || '').toUpperCase() === 'TRUE').length;
            const total = customers.length;
            const percent = total ? Math.round((completed / total) * 100) : 0;
            return { name, total, completed, remaining: Math.max(total - completed, 0), percent };
        });
        campaignsBody.innerHTML = campaignProgress.length
            ? campaignProgress.map(progress => `
                <div>
                    <div class="flex items-center justify-between gap-4 mb-2">
                        <span class="text-sm font-medium text-brandDark truncate">${escapeHtml(progress.name)}</span>
                        <span class="text-xs font-medium text-brandDark/60 shrink-0">${progress.completed}/${progress.total} calls</span>
                    </div>
                    <div class="w-full bg-slate-100 rounded-full h-2.5">
                        <div class="bg-brandAmber h-2.5 rounded-full transition-all duration-500" style="width: ${progress.percent}%"></div>
                    </div>
                    <div class="flex justify-between mt-1.5 text-[11px] text-brandDark/50">
                        <span>${progress.percent}% complete</span>
                        <span>${progress.remaining} remaining</span>
                    </div>
                </div>`).join('')
            : '<p class="text-sm text-brandDark/50 italic py-2">No campaigns found.</p>';
    }
};

// Make sure to call these in your data fetch callbacks!

const originalFetchAgents = window.fetchAgentsData;
window.fetchAgentsData = async function() {
    await originalFetchAgents();
    renderOverviewData();
};

const originalFetchCustomers = window.fetchCustomersData;
window.fetchCustomersData = async function() {
    await originalFetchCustomers();
    renderOverviewData();
};

// --- DARK MODE LOGIC ---
window.toggleDarkMode = function() {
    const htmlEl = document.documentElement;
    const isDark = typeof arguments[0] === 'boolean' ? arguments[0] : !htmlEl.classList.contains('dark');
    htmlEl.classList.toggle('dark', isDark);
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    document.querySelectorAll('.dark-mode-toggle').forEach(toggle => {
        toggle.checked = isDark;
    });
};

// ONLY apply Dark Mode if the user specifically clicked the toggle previously
if (localStorage.getItem('theme') === 'dark') {
    document.documentElement.classList.add('dark');
} else {
    document.documentElement.classList.remove('dark'); // Forces Light Mode by default
}
document.querySelectorAll('.dark-mode-toggle').forEach(toggle => {
    toggle.checked = document.documentElement.classList.contains('dark');
});

const CUSTOMER_DISPLAY_COLUMNS = [
    ['id', 'ID'], ['name', 'Customer Name'], ['phone', 'Phone'],
    ['branch', 'Branch'], ['sector', 'Sector'], ['balance', 'Balance'],
    ['campaign', 'Campaign'], ['agentId', 'Assigned Agent'],
    ['worked', 'Worked'], ['outcome', 'Outcome'], ['status', 'Status'], ['businessStatus', 'Business Status']
];

function displayValue(value) {
    return value === 0 || value === false ? String(value) : (value || '--');
}

const CATEGORY_DISPLAY_COLUMNS = {
    defaulted: [['name', 'Name'], ['phone', 'Mobile No'], ['dueDate', 'Due Date'], ['branch', 'Branch'], ['pair', 'Pair'], ['sector', 'Sector'], ['disbAmount', 'Disb Amount'], ['totalPaid', 'Total Paid'], ['balance', 'Balance'], ['businessStatus', 'Business Status']],
    upcoming_dues: [['name', 'Name'], ['phone', 'Mobile No'], ['dueDate', 'Due Date'], ['branch', 'Branch'], ['pair', 'Pair'], ['sector', 'Sector'], ['disbAmount', 'Disb Amount'], ['totalPaid', 'Total Paid'], ['balance', 'Balance'], ['businessStatus', 'Business Status']],
    active_no_loan: [
        ['sector', 'Sector'], ['branch', 'Station'], ['name', 'Customer'], ['phone', 'Mobile No'],
        ['pair', 'Pair'], ['daysInactive', 'Days Inactive'], ['loyalty', 'Loyalty'],
        ['lastLoanAmount', 'Lastloan Amount'], ['outcome', 'Call Outcome'], ['status', 'Status'], ['businessStatus', 'Business Status'], ['feedback', 'Feedback']
    ],
    dormant: [
        ['sector', 'Sector'], ['branch', 'Station'], ['name', 'Customer'], ['phone', 'Mobile No'],
        ['pair', 'Pair'], ['daysDormant', 'Days Dormant'], ['loyalty', 'Loyalty'],
        ['lastLoanAmount', 'Lastloan Amount'], ['outcome', 'Call Outcome'], ['status', 'Status'], ['businessStatus', 'Business Status'], ['feedback', 'Feedback']
    ]
};

function customerColumns(customer) {
    const type = customerCampaignType(customer);
    return CATEGORY_DISPLAY_COLUMNS[type] || CUSTOMER_DISPLAY_COLUMNS;
}

async function claimNextCustomer() {
    if (!LOGGED_IN_AGENT) return;
    if (CURRENT_USER_ROLE !== 'Admin') {
        showAppAlert('Only Admin users can assign accounts.', 'Permission Denied');
        return;
    }
    try {
        const response = await fetch(`${API_BASE}/claim-next-customer`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agentName: LOGGED_IN_AGENT, requesterRole: CURRENT_USER_ROLE })
        });
        if (!response.ok) {
            const errorBody = await response.json().catch(() => ({}));
            throw new Error(errorBody.detail || `Status ${response.status}`);
        }
        const customer = await response.json();
        mockCustomers.unshift(customer);
        window.customers = mockCustomers;
        renderAgentQueue();
    } catch (error) {
        console.error('Failed to assign customer:', error);
        showAppAlert('Could not assign an account to you. Please try again.', 'Assignment Error');
    }
}