class MediaManager {
    constructor() {
        this.activeVideos = new Map();
        this.loadingStates = new Map();
        this.observer = null;
        this.initIntersectionObserver();
    }

    initIntersectionObserver() {
        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const container = entry.target;
                const videoElement = container.querySelector('video');
                if (!videoElement) return;
    
                if (entry.isIntersecting) {
                    console.log('Video entering viewport:', container.dataset.postId);
                    this.initializeVideo(container, videoElement);
                    // Try to play immediately if it's already loaded
                    if (videoElement.readyState >= 3) {
                        videoElement.play().catch(error => {
                            console.log('Auto-play failed:', error);
                            this.showPlayButton(container);
                        });
                    }
                } else {
                    console.log('Video leaving viewport:', container.dataset.postId);
                    this.cleanupVideo(container, videoElement);
                }
            });
        }, {
            rootMargin: '50px 0px', // Load sooner
            threshold: 0.5 // More aggressive threshold
        });
    }
    
    initializeVideo(container, video) {
        if (this.activeVideos.has(video)) {
            console.log('Video already initialized:', container.dataset.postId);
            return;
        }
        
        const postId = container.closest('.single-view-post').dataset.postId;
        console.log('Initializing video for post:', postId);
        this.loadingStates.set(postId, 'loading');
        
        // Setup video with minimal initial state
        video.muted = true;
        video.playsInline = true;
        video.loop = true;
        video.autoplay = true; // Add autoplay
        
        // Create and cache player instance
        const player = {
            element: video,
            container: container,
            playing: false,
            muted: true
        };
        
        this.activeVideos.set(video, player);
        
        // Defer play attempt until loaded
        video.addEventListener('loadeddata', () => {
            console.log('Video loaded:', postId);
            this.loadingStates.set(postId, 'ready');
            container.classList.add('loaded');
            // Automatically try to play
            video.play().catch((error) => {
                console.log('Auto-play failed:', error);
                this.showPlayButton(container);
            });
        });
    
        video.addEventListener('error', (e) => {
            console.error('Video error:', e.target.error);
        });
    
        // Start loading the video
        if (video.dataset.src) {
            console.log('Setting video source:', video.dataset.src);
            video.src = video.dataset.src;
            video.load();
        } else {
            console.error('No video source found for:', postId);
        }
    }

    cleanupVideo(container, video) {
        const player = this.activeVideos.get(video);
        if (!player) return;

        video.pause();
        video.currentTime = 0;
        video.src = '';
        video.load();

        this.activeVideos.delete(video);
        const postId = container.closest('.single-view-post').dataset.postId;
        this.loadingStates.delete(postId);
        container.classList.remove('loaded');
    }

    isElementVisible(el) {
        const rect = el.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= window.innerHeight &&
            rect.right <= window.innerWidth
        );
    }

    showPlayButton(container) {
        const playIndicator = container.querySelector('.play-indicator');
        if (playIndicator) {
            playIndicator.style.opacity = '1';
        }
    }

    observeContainer(container) {
        console.log('Observing container:', container.closest('.single-view-post').dataset.postId);
        this.observer.observe(container);
    }

    cleanup() {
        this.activeVideos.forEach((player, video) => {
            this.cleanupVideo(player.container, video);
        });
        this.observer.disconnect();
    }
}

class SubredditViewer {
    constructor() {
        this.subreddit = window.location.pathname.split('/')[2];
        this.container = document.getElementById('content-container');
        this.viewMode = window.innerWidth <= 768 ? 'single' : (localStorage.getItem('preferredView') || 'single');
        this.sortType = localStorage.getItem('preferredSort') || 'score';
        this.posts = [];
        this.checkInterval = null;
        this.currentObserver = null;
        this.activeVideoPlayers = new Set();
        this.scrollListener = null;
        this.mediaManager = new MediaManager();
    }

    isMobile() {
        return window.innerWidth <= 768 || 
               /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    }

    async initialize() {
        try {
            if (this.isMobile()) {
                this.viewMode = 'single';
                const viewModeSelect = document.getElementById('view-mode');
                viewModeSelect.value = 'single';
                viewModeSelect.disabled = true;
            }

            document.getElementById('current-subreddit').textContent = `r/${this.subreddit}`;
            document.title = `r/${this.subreddit} - Redditarr`;
            
            const viewSelector = document.getElementById('view-mode');
            viewSelector.value = this.viewMode;
            viewSelector.addEventListener('change', async (e) => {
                const overlay = document.createElement('div');
                overlay.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
                overlay.innerHTML = `
                    <div class="bg-white p-4 rounded-lg shadow-lg">
                        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
                    </div>
                `;
                document.body.appendChild(overlay);

                try {
                    this.viewMode = e.target.value;
                    localStorage.setItem('preferredView', this.viewMode);
                    await this.renderPosts();
                } catch (error) {
                    console.error('Error switching views:', error);
                    alert('Error switching views. Please try refreshing the page.');
                } finally {
                    overlay.remove();
                }
            });

            const sortSelector = document.getElementById('sort-type');
            sortSelector.value = this.sortType;
            sortSelector.addEventListener('change', async (e) => {
                const sortMap = {
                    'score': 'score',
                    'new': 'new',
                    'random': 'random'
                };
                this.sortType = e.target.value;
                localStorage.setItem('preferredSort', this.sortType);
                await this.loadPosts();
            });

            await this.loadPosts();
        } catch (error) {
            console.error('Error initializing viewer:', error);
            this.showMessage('Error initializing viewer. Please try refreshing the page.');
        }
    }

    async cleanup() {
        if (this.mediaManager) {
            this.mediaManager.cleanup();
        }
    }

    async cleanupCurrentView() {
        if (this.currentObserver) {
            this.currentObserver.disconnect();
            this.currentObserver = null;
        }

        this.activeVideoPlayers.forEach(video => {
            video.pause();
            video.removeAttribute('src');
            video.load();
        });
        this.activeVideoPlayers.clear();

        if (this.scrollListener) {
            window.removeEventListener('scroll', this.scrollListener);
            this.scrollListener = null;
        }

        this.container.innerHTML = '';
    }

    async loadPosts() {
        try {
            const response = await fetch(
                `/api/subreddits/${this.subreddit}/posts?sort=${this.sortType}&view_mode=${this.viewMode}`
            );
            if (!response.ok) throw new Error('Failed to load posts');
            this.posts = await response.json();
            await this.renderPosts();
        } catch (error) {
            console.error('Error loading posts:', error);
            this.showMessage('Error loading posts. Please try again later.');
        }
    }

    async renderPosts() {
        await this.cleanupCurrentView();
        
        // Add loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.className = 'flex items-center justify-center p-8';
        loadingIndicator.innerHTML = `
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        `;
        this.container.appendChild(loadingIndicator);
    
        try {
            if (this.viewMode === 'single') {
                this.container.className = 'single-view-container';
                // Clear the loading indicator before rendering
                this.container.innerHTML = '';
                await this.renderSingleView();
            } else if (this.viewMode === 'grid') {
                this.container.className = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6';
                // Clear the loading indicator before rendering
                this.container.innerHTML = '';
                await this.renderGridView();
            } else {
                this.container.className = 'space-y-4';
                // Clear the loading indicator before rendering
                this.container.innerHTML = '';
                await this.renderRedditView();
            }
        } catch (error) {
            console.error('Error rendering posts:', error);
            this.container.innerHTML = `
                <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
                    Error loading posts. Please try refreshing the page.
                </div>
            `;
        }
    }

    async renderRedditView() {
        const POSTS_PER_PAGE = 10;
        let currentIndex = 0;

        const loadMorePosts = async () => {
            const postsToRender = this.posts.slice(currentIndex, currentIndex + POSTS_PER_PAGE);
            if (postsToRender.length === 0) return;

            const fragment = document.createDocumentFragment();
            postsToRender.forEach(post => {
                const postElement = document.createElement('div');
                postElement.innerHTML = this.renderPost(post);
                fragment.appendChild(postElement.firstChild);
            });

            this.container.appendChild(fragment);
            currentIndex += POSTS_PER_PAGE;
        };

        await loadMorePosts();

        this.scrollListener = async () => {
            if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 1000) {
                await loadMorePosts();
            }
        };
        window.addEventListener('scroll', this.scrollListener);
    }

    renderSingleView() {
        const nav = document.createElement('div');
        nav.className = 'single-view-nav';
        nav.innerHTML = `
            <div class="flex items-center space-x-4">
                <a href="/" class="text-white hover:text-gray-300">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                    </svg>
                </a>
                <span class="font-medium">r/${this.subreddit}</span>
            </div>
            <div class="flex items-center space-x-4">
                <select id="single-view-sort" class="bg-transparent border border-gray-600 rounded px-2 py-1 text-sm">
                    <option value="score" ${this.sortType === 'score' ? 'selected' : ''}>Top Votes</option>
                    <option value="new" ${this.sortType === 'new' ? 'selected' : ''}>Newest First</option>
                    <option value="random" ${this.sortType === 'random' ? 'selected' : ''}>Random</option>
                </select>
                <select id="single-view-mode" class="bg-transparent border border-gray-600 rounded px-2 py-1 text-sm">
                    <option value="reddit" ${this.viewMode === 'reddit' ? 'selected' : ''}>Reddit Style</option>
                    <option value="single" ${this.viewMode === 'single' ? 'selected' : ''}>Single View</option>
                    <option value="grid" ${this.viewMode === 'grid' ? 'selected' : ''}>Grid View</option>
                </select>
            </div>
        `;
    
        const viewportContainer = document.createElement('div');
        viewportContainer.className = 'single-view-viewport';
        
        this.posts.forEach((post, index) => {
            const postContainer = document.createElement('div');
            postContainer.className = 'single-view-post';
            postContainer.innerHTML = this.renderSinglePost(post);
            viewportContainer.appendChild(postContainer);
        });
        
        this.container.appendChild(nav);
        this.container.appendChild(viewportContainer);
    
        const viewModeSelect = document.getElementById('single-view-mode');
        viewModeSelect.addEventListener('change', (e) => {
            this.viewMode = e.target.value;
            localStorage.setItem('preferredView', this.viewMode);
            this.renderPosts();
        });
    
        const sortSelect = document.getElementById('single-view-sort');
        sortSelect.addEventListener('change', (e) => {
            this.sortType = e.target.value;
            localStorage.setItem('preferredSort', this.sortType);
            this.loadPosts();
        });
    
        this.initializeSingleViewInteractions();
    }

    renderSinglePost(post) {
        const hasMedia = post.media_items && post.media_items.length > 0;
        const mediaItem = hasMedia ? post.media_items[0] : null;
        const mediaPath = mediaItem?.download_path || '';
    
        return `
            <div class="single-view-post" data-post-id="${post.id}">
                <div class="single-view-media">
                    ${mediaItem?.media_type === 'video' ? `
                        <div class="video-container relative w-full h-full">
                            <video 
                                class="media-player w-full h-full object-contain"
                                data-src="${mediaPath}"
                                loop 
                                muted 
                                playsinline
                            ></video>
                            
                            <div class="loading-indicator absolute inset-0 flex items-center justify-center bg-black bg-opacity-40">
                                <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-white"></div>
                            </div>
                            
                            <div class="video-controls absolute bottom-0 right-0 p-4 flex items-center space-x-4 text-white opacity-0">
                                <button class="audio-toggle opacity-80 hover:opacity-100 transition-opacity">
                                    <svg class="w-6 h-6 audio-off-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
                                    </svg>
                                    <svg class="w-6 h-6 audio-on-icon hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                                    </svg>
                                </button>
                            </div>
                            
                            <div class="play-indicator absolute inset-0 flex items-center justify-center bg-black bg-opacity-40 opacity-0 transition-opacity duration-200">
                                <svg class="w-16 h-16 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                                </svg>
                            </div>
                        </div>
                    ` : mediaPath ? `
                        <img 
                            src="${mediaPath}"
                            alt="${post.title}"
                            loading="lazy"
                            class="w-full h-full object-contain"
                        >
                    ` : `
                        <div class="flex items-center justify-center text-white">
                            ${post.downloaded ? 'No Preview Available' : 'Download Pending'}
                        </div>
                    `}
                </div>
                <div class="single-view-info">
                    <div class="text-sm opacity-75 mb-1">
                        Posted by u/${post.author}
                    </div>
                    <div class="text-lg font-medium mb-2">
                        ${post.title}
                    </div>
                    <div class="flex items-center space-x-4">
                        <div class="flex items-center space-x-1">
                            <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M2 10.5a1.5 1.5 0 113 0v6a1.5 1.5 0 01-3 0v-6zM6 10.333v5.43a2 2 0 001.106 1.79l.05.025A4 4 0 008.943 18h5.416a2 2 0 001.962-1.608l1.2-6A2 2 0 0015.56 8H12V4a2 2 0 00-2-2 1 1 0 00-1 1v.667a4 4 0 01-.8 2.4L6.8 7.933a4 4 0 00-.8 2.4z" />
                            </svg>
                            <span>${this.formatScore(post.score)}</span>
                        </div>
                        ${post.downloaded ? `
                            <div class="text-green-400">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                                </svg>
                            </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    initializeSingleViewInteractions() {
        const viewport = this.container.querySelector('.single-view-viewport');
        const videoContainers = viewport.querySelectorAll('.video-container');
        
        console.log('Found video containers:', videoContainers.length);
        
        videoContainers.forEach(container => {
            this.mediaManager.observeContainer(container);
            
            // Minimal click handlers for controls
            const audioToggle = container.querySelector('.audio-toggle');
            if (audioToggle) {
                audioToggle.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const video = container.querySelector('video');
                    if (!video) return;
                    
                    const player = this.mediaManager.activeVideos.get(video);
                    if (player) {
                        player.muted = !player.muted;
                        video.muted = player.muted;
                        container.querySelector('.audio-on-icon').classList.toggle('hidden', player.muted);
                        container.querySelector('.audio-off-icon').classList.toggle('hidden', !player.muted);
                    }
                });
            }
    
            container.addEventListener('click', () => {
                const video = container.querySelector('video');
                if (!video) return;
                
                if (video.paused) {
                    video.play().catch(error => {
                        console.log('Play failed on click:', error);
                        this.mediaManager.showPlayButton(container);
                    });
                } else {
                    video.pause();
                    this.mediaManager.showPlayButton(container);
                }
            });
        });
    
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (!this.container.contains(document.activeElement)) return;
            
            const currentScroll = viewport.scrollTop;
            const postHeight = viewport.clientHeight;
            
            if (e.key === 'ArrowUp' || e.key === 'k') {
                viewport.scrollTo({
                    top: currentScroll - postHeight,
                    behavior: 'smooth'
                });
            } else if (e.key === 'ArrowDown' || e.key === 'j') {
                viewport.scrollTo({
                    top: currentScroll + postHeight,
                    behavior: 'smooth'
                });
            }
        });
    }

    renderGridView() {
        // TODO: Implement grid view
        this.container.innerHTML = '<div class="text-center text-gray-500">Grid view coming soon</div>';
    }

    renderPost(post) {
        const hasMedia = post.media_items && post.media_items.length > 0;
        const isTextPost = post.post_type === 'text';
        
        return `
            <div class="bg-white rounded-lg shadow hover:border-gray-300 cursor-pointer">
                <div class="px-8 pt-4 pb-2 border-b border-gray-100">
                    <div class="text-xs text-gray-500 mb-2">
                        Posted by u/${post.author} • ${this.formatDate(post.created_utc)}
                    </div>
                    <h2 class="text-lg font-medium mb-2">${post.title}</h2>
                </div>

                <div class="px-8 py-4">
                    ${isTextPost ? `
                        <div class="prose max-w-none">
                            ${post.selftext ? marked.parse(post.selftext) : ''}
                        </div>
                    ` : hasMedia ? this.renderMediaContent(post) : `
                        <div class="text-sm text-gray-500">
                            External Link
                        </div>
                    `}
                </div>

                <div class="px-8 py-2 border-t border-gray-100 flex items-center space-x-4 text-sm text-gray-500">
                    <div class="flex items-center space-x-1">
                        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                            <path d="M2 10.5a1.5 1.5 0 113 0v6a1.5 1.5 0 01-3 0v-6zM6 10.333v5.43a2 2 0 001.106 1.79l.05.025A4 4 0 008.943 18h5.416a2 2 0 001.962-1.608l1.2-6A2 2 0 0015.56 8H12V4a2 2 0 00-2-2 1 1 0 00-1 1v.667a4 4 0 01-.8 2.4L6.8 7.933a4 4 0 00-.8 2.4z" />
                        </svg>
                        <span>${this.formatScore(post.score)} points</span>
                    </div>

                    ${post.downloaded ? `
                        <div class="text-green-600 flex items-center space-x-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                            </svg>
                            <span>Archived</span>
                        </div>
                    ` : `
                        <div class="text-yellow-600 flex items-center space-x-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>Download Pending</span>
                        </div>
                    `}
                </div>
            </div>
        `;
    }

    renderMediaContent(post) {
        const mediaItems = post.media_items || [];
        if (!mediaItems.length) {
            return `
                <div class="w-full h-64 bg-gray-100 flex items-center justify-center text-gray-500">
                    ${post.downloaded ? 'No Preview' : 'Download Pending'}
                </div>
            `;
        }

        // For now, just show the first media item
        const item = mediaItems[0];
        const mediaPath = item.download_path ? 
            (item.download_path.startsWith('/') ? item.download_path : `/${item.download_path}`) : 
            '';

        if (item.media_type === 'video') {
            return `
                <div class="relative pt-[56.25%]">
                    <video 
                        src="${mediaPath}"
                        class="absolute top-0 left-0 w-full h-full object-contain bg-black media-player"
                        loop 
                        muted 
                        playsinline 
                        controls
                    >
                        <source src="${mediaPath}" type="video/mp4">
                    </video>
                    ${!mediaPath ? `
                        <div class="absolute top-0 left-0 w-full h-full flex items-center justify-center bg-gray-100 text-gray-500">
                            Download Pending
                        </div>
                    ` : ''}
                </div>
            `;
        }
        
        return `
            <div class="relative pt-[75%]">
                <img 
                    src="${mediaPath}"
                    alt="${post.title}"
                    class="absolute top-0 left-0 w-full h-full object-contain bg-gray-50"
                    loading="lazy"
                >
                ${!mediaPath ? `
                    <div class="absolute top-0 left-0 w-full h-full flex items-center justify-center bg-gray-100 text-gray-500">
                        Download Pending
                    </div>
                ` : ''}
            </div>
        `;
    }

    formatScore(score) {
        if (score >= 1000000) return `${(score/1000000).toFixed(1)}M`;
        if (score >= 1000) return `${(score/1000).toFixed(1)}K`;
        return score.toString();
    }

    formatDate(timestamp) {
        return new Date(timestamp * 1000).toLocaleString();
    }

    showMessage(message) {
        this.container.innerHTML = `
            <div class="bg-white rounded-lg shadow-lg p-8 max-w-2xl mx-auto text-center">
                <p class="text-gray-600">${message}</p>
            </div>
        `;
    }
}

// Initialize the viewer
const viewer = new SubredditViewer();
document.addEventListener('DOMContentLoaded', () => viewer.initialize());