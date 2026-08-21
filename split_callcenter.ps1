$ErrorActionPreference = 'Stop'
$html = Get-Content -Raw cc.html

$styleMatch = [regex]::Match($html, '(?s)<style>\s*(.*?)\s*</style>')
if (-not $styleMatch.Success) { throw 'Style block not found' }
Set-Content -Path callcenter.css -Value $styleMatch.Groups[1].Value.Trim() -Encoding UTF8

$scriptMatches = [regex]::Matches($html, '(?s)<script>(.*?)</script>')
if ($scriptMatches.Count -lt 2) { throw 'Expected Tailwind config and app script blocks' }
$tailwindConfig = $scriptMatches[0].Groups[1].Value.Trim()
$appScript = $scriptMatches[$scriptMatches.Count - 1].Groups[1].Value.Trim()

$statePattern = 'const mockCustomers = \[\];\s*const campaignConfigs = \{\};\s*\r?\n\s*// Dynamic Agent List starts empty as requested\s*\r?\n\s*let agents = \[\];\s*let LOGGED_IN_AGENT = null;\s*\r?\n\s*let isClockedIn = false;\s*\r?\n\s*let activeCustomerId = null;\s*\r?\n\s*let activeWorkspaceQueueTab = ''active'';\s*\r?\n\s*let globalStats = \{\s*totalCalls: 0,\s*connected: 0,\s*recovered: 0,\s*outcomes: \{ "Answered": 0, "Unanswered": 0, "Offline": 0, "Third party": 0, "Voicemail": 0 \}\s*\};'
$stateReplacement = @'
    const STORAGE_KEY = 'osaCallCenterState';
    const savedState = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const mockCustomers = savedState.mockCustomers || [];
    const campaignConfigs = savedState.campaignConfigs || {};

    let agents = savedState.agents || [];
    let LOGGED_IN_AGENT = savedState.LOGGED_IN_AGENT || null;
    let isClockedIn = false;
    let activeCustomerId = null;
    let activeWorkspaceQueueTab = savedState.activeWorkspaceQueueTab || 'active';

    let globalStats = savedState.globalStats || {
      totalCalls: 0,
      connected: 0,
      recovered: 0,
      outcomes: { "Answered": 0, "Unanswered": 0, "Offline": 0, "Third party": 0, "Voicemail": 0 }
    };

    function saveAppState() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        mockCustomers,
        campaignConfigs,
        agents,
        LOGGED_IN_AGENT,
        activeWorkspaceQueueTab,
        globalStats
      }));
    }
'@
$appScript = [regex]::Replace($appScript, $statePattern, $stateReplacement)

$onloadPattern = 'window\.onload = \(\) => \{\s*renderAgentDropdown\(\);\s*renderShiftManager\(\);\s*renderTeamLeaderWorkspace\(\);\s*updateAnalyticsUI\(\);\s*\};'
$onloadReplacement = @'
    window.onload = () => {
        initCurrentPage();
    };

    function initCurrentPage() {
        renderAgentDropdown();
        renderShiftManager();
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
'@
$appScript = [regex]::Replace($appScript, $onloadPattern, $onloadReplacement)

$switchPattern = 'function switchView\(viewId, btnElement\) \{(?s).*?\n    \}\r?\n\r?\n    // --- AGENT WORKSPACE \(QUEUE\) LOGIC ---'
$switchReplacement = @'
    function switchView(viewId) {
      const pageMap = {
        workspace: 'workspace.html',
        campaigns: 'campaigns.html',
        teamleader: 'teamleader.html',
        dashboard: 'analytics.html'
      };
      window.location.href = pageMap[viewId] || 'workspace.html';
    }

    // --- AGENT WORKSPACE (QUEUE) LOGIC ---
'@
$appScript = [regex]::Replace($appScript, $switchPattern, $switchReplacement)

$appScript = $appScript -replace 'updateAnalyticsUI\(\); // Updates the Active Agents KPI', "updateAnalyticsUI(); // Updates the Active Agents KPI`r`n       saveAppState();"
$appScript = $appScript -replace 'submitBtn\.innerHTML = originalText;\s*\r?\n\s*\}, 600\);', "submitBtn.innerHTML = originalText;`r`n        saveAppState();`r`n      }, 600);"
$appScript = $appScript -replace 'alert\(`Rescheduled \$\{c\.name\}\. It is now in the Workspace Pending tab\$\{c\.agentId \? '''' : '' and can be reassigned''\}\.`\);', "saveAppState();`r`n      alert(``Rescheduled `${c.name}. It is now in the Workspace Pending tab`${c.agentId ? '' : ' and can be reassigned'}.``);"
$appScript = $appScript -replace 'alert\(`Redistributed \$\{assignedCount\} pending callback\(s\) to active free agents\.`\);', "saveAppState();`r`n        alert(``Redistributed `${assignedCount} pending callback(s) to active free agents.``);"
$appScript = $appScript -replace 'alert\(`Success: Distributed \$\{assignedCount\} customers evenly across \$\{activeAgents\.length\} selected agent\(s\)\.`\);', "saveAppState();`r`n      alert(``Success: Distributed `${assignedCount} customers evenly across `${activeAgents.length} selected agent(s).``);"

$appScript = $appScript -replace 'function updateWorkspaceStats\(\) \{\s*const agent = agents\.find\(a => a\.name === LOGGED_IN_AGENT\);', "function updateWorkspaceStats() {`r`n       if (!document.getElementById('ws-stats-calls')) return;`r`n       const agent = agents.find(a => a.name === LOGGED_IN_AGENT);"
$appScript = $appScript -replace 'function renderAgentQueue\(\) \{\s*if \(!LOGGED_IN_AGENT\) return;\s*const queueDiv = document\.getElementById\(''agent-customer-list''\);', "function renderAgentQueue() {`r`n        const queueDiv = document.getElementById('agent-customer-list');`r`n        if (!queueDiv || !LOGGED_IN_AGENT) return;"
$appScript = $appScript -replace 'function updateAnalyticsUI\(\) \{\s*syncAgentAssignments\(\);\s*updateResponseCampaignFilter\(\);', "function updateAnalyticsUI() {`r`n        syncAgentAssignments();`r`n        updateResponseCampaignFilter();`r`n        if (!document.getElementById('dash-total-calls')) return;"
$appScript = $appScript -replace 'function renderCampaignList\(\) \{\s*const tbody = document\.getElementById\(''campaign-list-tbody''\);\s*tbody\.innerHTML = '''';', "function renderCampaignList() {`r`n        const tbody = document.getElementById('campaign-list-tbody');`r`n        if (!tbody) return;`r`n        tbody.innerHTML = '';"
$appScript = $appScript -replace 'const allocSelect = document\.getElementById\(''alloc-campaign''\);\s*\r?\n\s*if\(uniqueCampaigns\.length === 0\) \{', "const allocSelect = document.getElementById('alloc-campaign');`r`n      if(!allocSelect) { updateResponseCampaignFilter(); return; }`r`n      if(uniqueCampaigns.length === 0) {"

Set-Content -Path callcenter.js -Value $appScript -Encoding UTF8

$background = [regex]::Match($html, '(?s)  <!-- BACKGROUND -->.*?  <!-- ADD CAMPAIGN MODAL -->').Value -replace '\s*<!-- ADD CAMPAIGN MODAL -->\s*$', "`r`n"
$campaignModal = [regex]::Match($html, '(?s)  <!-- ADD CAMPAIGN MODAL -->.*?  <!-- SLIDE-OUT CUSTOMER DRAWER -->').Value -replace '\s*<!-- SLIDE-OUT CUSTOMER DRAWER -->\s*$', "`r`n"
$drawer = [regex]::Match($html, '(?s)  <!-- SLIDE-OUT CUSTOMER DRAWER -->.*?  <!-- SIDEBAR -->').Value -replace '\s*<!-- SIDEBAR -->\s*$', "`r`n"
$header = [regex]::Match($html, '(?s)    <!-- TOP BAR -->.*?    <!-- CONTENT VIEWS WRAPPER -->').Value -replace '\s*<!-- CONTENT VIEWS WRAPPER -->\s*$', "`r`n"

$views = @{}
$views.workspace = [regex]::Match($html, '(?s)      <!-- ========================================== -->\s*<!-- VIEW 1: AGENT WORKSPACE -->.*?\n      <!-- ========================================== -->\s*<!-- VIEW 2: CAMPAIGNS').Value -replace '\s*<!-- ========================================== -->\s*<!-- VIEW 2: CAMPAIGNS$', ''
$views.campaigns = [regex]::Match($html, '(?s)      <!-- ========================================== -->\s*<!-- VIEW 2: CAMPAIGNS & SHIFT MANAGER -->.*?\n      <!-- ========================================== -->\s*<!-- VIEW 3: TEAM LEADER').Value -replace '\s*<!-- ========================================== -->\s*<!-- VIEW 3: TEAM LEADER$', ''
$views.teamleader = [regex]::Match($html, '(?s)      <!-- ========================================== -->\s*<!-- VIEW 3: TEAM LEADER WORKSPACE -->.*?\n      <!-- ========================================== -->\s*<!-- VIEW 4: DEEP ANALYTICS').Value -replace '\s*<!-- ========================================== -->\s*<!-- VIEW 4: DEEP ANALYTICS$', ''
$views.analytics = [regex]::Match($html, '(?s)      <!-- ========================================== -->\s*<!-- VIEW 4: DEEP ANALYTICS DASHBOARD -->.*?\n\s*</div>\s*</main>').Value -replace '\s*</div>\s*</main>$', ''

function New-Sidebar($active) {
  $items = @(
    @{Page='workspace'; Href='workspace.html'; Icon='fa-headset'; Text='Workspace'},
    @{Page='campaigns'; Href='campaigns.html'; Icon='fa-bullhorn'; Text='Campaigns'},
    @{Page='teamleader'; Href='teamleader.html'; Icon='fa-user-tie'; Text='Team Lead'},
    @{Page='dashboard'; Href='analytics.html'; Icon='fa-chart-pie'; Text='Analytics'}
  )
  $out = "  <!-- SIDEBAR -->`r`n  <aside class=""z-10 w-20 md:w-24 glass-panel border-r flex flex-col items-center py-6"">`r`n"
  foreach ($item in $items) {
    $isActive = $item.Page -eq $active
    $activeClass = if ($isActive) { ' bg-white/40' } else { '' }
    $indicatorClass = if ($isActive) { '' } else { ' hidden' }
    $out += "    <a href=""$($item.Href)"" data-page=""$($item.Page)"" class=""nav-btn w-full py-4 flex flex-col items-center gap-1.5 text-[11px] font-semibold text-brandDark/70 hover:text-brandDark transition-colors relative$activeClass"">`r`n"
    $out += "      <i class=""fa-solid $($item.Icon) text-[20px]""></i> $($item.Text)`r`n"
    $out += "      <div class=""active-indicator absolute left-0 top-0 h-full w-1 bg-brandAmber$indicatorClass""></div>`r`n"
    $out += "    </a>`r`n"
  }
  $out += "  </aside>`r`n"
  return $out
}

function New-Page($file, $title, $navKey, $viewHtml) {
  $sidebar = New-Sidebar $navKey
  $viewHtml = $viewHtml -replace '(<div id="view-[^"]+" class="view) hidden', '$1'
  $content = @"
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>$title</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
$tailwindConfig
  </script>
  <link rel="stylesheet" href="callcenter.css">
</head>
<body data-page="$navKey" class="text-brandDark font-sans font-normal h-screen overflow-hidden flex relative bg-slate-50">
$background
$campaignModal
$drawer
$sidebar
  <main class="z-10 flex-1 flex flex-col p-6 overflow-hidden">
$header
    <div class="flex-1 overflow-hidden">
$viewHtml
    </div>
  </main>
  <script src="callcenter.js"></script>
</body>
</html>
"@
  Set-Content -Path $file -Value $content -Encoding UTF8
}

New-Page 'workspace.html' 'OSA Call Center - Workspace' 'workspace' $views.workspace
New-Page 'campaigns.html' 'OSA Call Center - Campaigns' 'campaigns' $views.campaigns
New-Page 'teamleader.html' 'OSA Call Center - Team Leader' 'teamleader' $views.teamleader
New-Page 'analytics.html' 'OSA Call Center - Analytics' 'dashboard' $views.analytics

Set-Content -Path cc.html -Value @'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0; url=workspace.html">
  <title>OSA Call Center</title>
  <script>window.location.replace('workspace.html');</script>
</head>
<body>
  <p>Opening <a href="workspace.html">OSA Call Center Workspace</a>...</p>
</body>
</html>
'@ -Encoding UTF8

Write-Host 'generated split pages'
