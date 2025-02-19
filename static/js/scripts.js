// static/js/scripts.js

// Utility Functions
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatScore(score) {
    if (score >= 1000000) return `${(score/1000000).toFixed(1)}M`;
    if (score >= 1000) return `${(score/1000).toFixed(1)}K`;
    return score.toString();
}

function formatDate(timestamp) {
    return new Date(timestamp * 1000).toLocaleString();
}

// Setup and Initialization
let setupAttempts = 0;
const MAX_SETUP_ATTEMPTS = 3;

async function checkAndRunSetup() {
    try {
        updateSetupStatus('Checking setup status...');
        const statusResponse = await fetch('/api/setup/status');
        const statusData = await statusResponse.json();

        if (!statusData.is_setup && setupAttempts < MAX_SETUP_ATTEMPTS) {
            setupAttempts++;
            updateSetupStatus('Running initial setup...', statusData);
            
            const setupResponse = await fetch('/api/setup/initialize', {
                method: 'POST'
            });
            
            if (!setupResponse.ok) {
                const error = await setupResponse.json();
                throw new Error(error.detail || 'Setup failed');
            }

            const setupData = await setupResponse.json();
            if (setupData.status !== 'success') {
                throw new Error('Setup verification failed');
            }

            updateSetupStatus('Setup complete!', setupData.details);
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            document.getElementById('setupOverlay').classList.add('hidden');
            document.getElementById('mainContent').classList.remove('hidden');
            
            initializeApp();
        } else if (statusData.is_setup) {
            document.getElementById('setupOverlay').classList.add('hidden');
            document.getElementById('mainContent').classList.remove('hidden');
            initializeApp();
        } else {
            throw new Error(`Setup failed after ${MAX_SETUP_ATTEMPTS} attempts`);
        }
    } catch (error) {
        console.error('Setup error:', error);
        const overlay = document.getElementById('setupOverlay');
        overlay.innerHTML = `
            <div class="bg-white p-8 rounded-lg shadow-xl text-center">
                <div class="text-red-500 mb-4 text-6xl">⚠️</div>
                <h2 class="text-xl font-semibold mb-2">Setup Failed</h2>
                <p class="text-gray-600 mb-4">${error.message}</p>
                <button onclick="location.reload()" 
                        class="bg-blue-500 text-white px-6 py-2 rounded hover:bg-blue-600">
                    Try Again
                </button>
            </div>
        `;
    }
}

function updateSetupStatus(message, details) {
    document.getElementById('setupStatus').textContent = message;
    if (details) {
        document.getElementById('dbStatus').textContent = details.db_initialized ? '✅' : '⌛';
        document.getElementById('nsfwStatus').textContent = details.nsfw_list_loaded ? '✅' : '⌛';
    }
}

async function initializeApp() {
    try {
        // Load initial NSFW mode setting
        const response = await fetch('/api/config/nsfw_mode');
        const { enabled } = await response.json();
        document.getElementById('nsfwMode').checked = enabled;
        document.getElementById('nsfwModeStatus').textContent = 
            enabled ? 'NSFW Mode Enabled' : 'SFW Mode Enabled';

        // Initialize subreddit select
        initializeSubredditSelect();
        
        // Load initial data
        await fetchSubreddits();
        
        // Set up event listeners
        setupEventListeners();
        
        // Load worker status
        await loadWorkerStatus();
        
    } catch (error) {
        console.error('Error initializing app:', error);
    }
}

// Subreddit Management
function initializeSubredditSelect() {
    $('#subredditSelect').select2({
        placeholder: 'Search for a subreddit...',
        minimumInputLength: 2,
        ajax: {
            url: '/api/subreddits/suggest',
            dataType: 'json',
            delay: 250,
            data: function(params) {
                return {
                    query: params.term
                };
            },
            processResults: function(data) {
                return {
                    results: data.map(sub => ({
                        id: sub.name,
                        text: `r/${sub.name}`,
                        subscribers: sub.metadata.subscribers,
                        description: sub.metadata.description,
                        metadata: sub.metadata
                    }))
                };
            },
            cache: true
        },
        templateResult: formatSubredditOption,
        templateSelection: formatSubredditSelection
    }).on('select2:select', function(e) {
        addSelectedSubreddit(e.params.data.id);
        $(this).val(null).trigger('change');
    });
}

function formatSubredditOption(subreddit) {
    if (!subreddit.id) return subreddit.text;
    
    return $(`
        <div class="p-2">
            <div class="flex items-center gap-2">
                <div class="font-bold">${subreddit.text}</div>
                <span class="text-sm text-gray-600">${formatSubscriberCount(subreddit.subscribers)}</span>
            </div>
            ${subreddit.description ? 
                `<div class="text-sm text-gray-600 mt-1">${subreddit.description}</div>` : 
                ''}
        </div>
    `);
}

function formatSubredditSelection(subreddit) {
    if (!subreddit.id) return subreddit.text;
    return `r/${subreddit.id}`;
}

function formatSubscriberCount(count) {
    if (!count) return '';
    if (count >= 1000000) return `${(count/1000000).toFixed(1)}M subscribers`;
    if (count >= 1000) return `${(count/1000).toFixed(1)}K subscribers`;
    return `${count} subscribers`;
}

async function addSelectedSubreddit(name) {
    try {
        const response = await fetch('/api/subreddits/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, should_monitor: true })
        });

        if (!response.ok) {
            throw new Error('Failed to add subreddit');
        }

        const data = await response.json();
        renderSubreddits(data);
        startPolling();
    } catch (error) {
        alert('Error adding subreddit: ' + error.message);
    }
}

async function fetchSubreddits() {
    try {
        const response = await fetch('/api/subreddits/');
        if (!response.ok) {
            throw new Error('Failed to fetch subreddits');
        }
        const subreddits = await response.json();
        renderSubreddits(subreddits);
    } catch (error) {
        console.error('Error fetching subreddits:', error);
    }
}

function getStatusClass(status) {
    const statusClasses = {
        'pending': 'bg-yellow-100 text-yellow-800',
        'indexing': 'bg-blue-100 text-blue-800',
        'ready': 'bg-green-100 text-green-800',
        'error': 'bg-red-100 text-red-800'
    };
    return statusClasses[status] || 'bg-gray-100 text-gray-800';
}

// Content Rendering
function renderSubreddits(subreddits) {
    const stats = subreddits.reduce((acc, sub) => ({
        totalPosts: acc.totalPosts + (sub.total_posts || 0),
        totalDownloaded: acc.totalDownloaded + (sub.downloaded_count || 0),
        totalSize: acc.totalSize + (sub.disk_usage || 0)
    }), { totalPosts: 0, totalDownloaded: 0, totalSize: 0 });

    document.getElementById('totalPosts').textContent = stats.totalPosts.toLocaleString();
    document.getElementById('totalDownloaded').textContent = stats.totalDownloaded.toLocaleString();
    document.getElementById('totalSize').textContent = formatBytes(stats.totalSize);
    document.getElementById('subredditCount').textContent = subreddits.length;

    const container = document.getElementById('subreddit-list');
    container.innerHTML = subreddits.map(sub => `
        <div class="bg-white p-4 rounded shadow hover:shadow-md transition-shadow">
            <div class="flex justify-between items-center mb-2">
                <a href="/r/${sub.name}" 
                   target="_blank" 
                   class="text-xl font-semibold hover:text-blue-600">
                    r/${sub.name}
                </a>
                <div class="flex gap-2 items-center">
                    <span class="px-2 py-1 rounded text-sm ${getStatusClass(sub.status)}">
                        ${sub.status}
                    </span>
                </div>
            </div>
            <div class="space-y-1 text-sm text-gray-600">
                <p>Posts: <span class="font-medium">${sub.total_posts || 0}</span></p>
                <p>Downloaded: <span class="font-medium">${sub.downloaded_count || 0}</span></p>
                <p>Size on Disk: <span class="font-medium">${formatBytes(sub.disk_usage || 0)}</span></p>
                <p>Last Updated: <span class="font-medium">
                    ${sub.last_indexed ? formatDate(sub.last_indexed) : 'Never'}
                </span></p>
                ${sub.error_message ? `
                    <p class="text-red-600 mt-2">Error: ${sub.error_message}</p>
                ` : ''}
            </div>
        </div>
    `).join('');
}

async function toggleNSFWMode(enabled) {
    try {
        const response = await fetch('/api/config/nsfw_mode', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ enabled })
        });
        
        if (!response.ok) {
            throw new Error('Failed to update NSFW mode');
        }
        
        // Clear subreddit select dropdown
        $('#subredditSelect').val(null).trigger('change');
        
        // Refresh the subreddit list
        await fetchSubreddits();
        
        document.getElementById('nsfwModeStatus').textContent = 
            enabled ? 'NSFW Mode Enabled' : 'SFW Mode Enabled';
        
    } catch (error) {
        console.error('Error updating NSFW mode:', error);
        alert('Failed to update NSFW mode');
    }
}

async function loadWorkerStatus() {
    try {
        const response = await fetch('/api/workers/status');
        const status = await response.json();
        
        document.getElementById('mediaWorker').checked = status.media;
        document.getElementById('mediaWorkerStatus').textContent = 
            status.media ? 'Worker Enabled' : 'Worker Disabled';
            
        document.getElementById('commentWorker').checked = status.comments;
        document.getElementById('commentWorkerStatus').textContent = 
            status.comments ? 'Worker Enabled' : 'Worker Disabled';
            
        document.getElementById('metadataWorker').checked = status.metadata;
        document.getElementById('metadataWorkerStatus').textContent = 
            status.metadata ? 'Worker Enabled' : 'Worker Disabled';
    } catch (error) {
        console.error('Error loading worker status:', error);
    }
}

// Event Listeners
function setupEventListeners() {
    document.getElementById('nsfwMode').addEventListener('change', function(e) {
        toggleNSFWMode(e.target.checked);
    });

    document.getElementById('metadataWorker').addEventListener('change', function(e) {
        toggleWorker('metadata', e.target.checked);
    });

    document.getElementById('mediaWorker').addEventListener('change', function(e) {
        toggleWorker('media', e.target.checked);
    });

    document.getElementById('commentWorker').addEventListener('change', function(e) {
        toggleWorker('comments', e.target.checked);
    });

    document.getElementById('sort-type').addEventListener('change', function(e) {
        const subreddit = document.getElementById('current-subreddit').textContent.slice(2);
        fetchPosts(subreddit, e.target.value);
    });
}

async function toggleWorker(type, enabled) {
    try {
        const response = await fetch(`/api/workers/${type}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ enabled })
        });

        const data = await response.json();  // Get response data first

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update worker status');
        }

        const statusElement = document.getElementById(`${type}WorkerStatus`);
        const checkbox = document.getElementById(`${type}Worker`);
        
        if (statusElement && checkbox) {  // Add null checks
            statusElement.textContent = enabled ? 'Worker Enabled' : 'Worker Disabled';
            checkbox.checked = enabled;
        }

    } catch (error) {
        console.error(`Error updating ${type} worker:`, error);
        // Revert checkbox state on error
        const checkbox = document.getElementById(`${type}Worker`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
        }
        alert(`Failed to update ${type} worker status: ${error.message}`);
    }
}

// Polling and Updates
let pollingInterval;

function startPolling() {
    if (!pollingInterval) {
        pollingInterval = setInterval(fetchSubreddits, 5000);
        setTimeout(() => {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }, 300000); // 5 minutes
    }
}

// Initialize setup
checkAndRunSetup();