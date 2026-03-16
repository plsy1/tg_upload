document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const setupForm = document.getElementById('setup-form');
    const verifyForm = document.getElementById('verify-form');
    const loginSection = document.getElementById('login-section');
    const uploadSection = document.getElementById('upload-section');
    const progressSection = document.getElementById('progress-section');
    const authStatus = document.getElementById('auth-status');
    const chatSelect = document.getElementById('chat-select');
    const folderPathInput = document.getElementById('folder-path');
    const startUploadBtn = document.getElementById('start-upload-btn');
    const progressBar = document.getElementById('main-progress');
    const progressCount = document.getElementById('progress-count');
    const currentFileName = document.getElementById('current-file-name');
    const logConsole = document.getElementById('log-console');

    let ws = null;

    // Helper: Add log
    function addLog(message, type = 'info') {
        const line = document.createElement('div');
        line.className = `log-line ${type}`;
        line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        logConsole.appendChild(line);
        logConsole.scrollTop = logConsole.scrollHeight;
    }

    // Step 1: Connect / Send Code
    setupForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData();
        formData.append('api_id', document.getElementById('api_id').value);
        formData.append('api_hash', document.getElementById('api_hash').value);
        formData.append('phone', document.getElementById('phone').value);

        try {
            const response = await fetch('/api/setup', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (data.status === 'needs_code') {
                setupForm.classList.add('hidden');
                verifyForm.classList.remove('hidden');
                addLog('验证码已发送，请查收 Telegram', 'info');
            } else if (data.status === 'authorized') {
                handleLoginSuccess();
            } else {
                addLog('错误: ' + (data.message || '未知错误'), 'error');
            }
        } catch (err) {
            addLog('网络错误: ' + err.message, 'error');
        }
    });

    // Step 2: Verify Code
    const verifyBtn = document.getElementById('verify-btn');
    verifyBtn.addEventListener('click', async () => {
        const codeInput = document.getElementById('code');
        const passwordInput = document.getElementById('password');
        const code = codeInput.value.trim();
        const password = passwordInput.value.trim();
        
        if (!code) {
            addLog('请输入验证码', 'error');
            return;
        }

        verifyBtn.disabled = true;
        verifyBtn.textContent = '正在验证...';
        addLog(`开始验证代码: ${code}...`, 'info');

        const formData = new FormData();
        formData.append('code', code);
        if (password) {
            formData.append('password', password);
        }

        try {
            const response = await fetch('/api/verify', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (data.status === 'authorized') {
                addLog('验证成功！', 'success');
                handleLoginSuccess();
            } else {
                addLog('验证失败: ' + (data.message || '未知错误'), 'error');
                verifyBtn.disabled = false;
                verifyBtn.textContent = '完成验证';
            }
        } catch (err) {
            addLog('网络请求异常: ' + err.message, 'error');
            verifyBtn.disabled = false;
            verifyBtn.textContent = '完成验证';
        }
    });

    async function handleLoginSuccess() {
        loginSection.classList.add('hidden');
        uploadSection.classList.remove('hidden');
        authStatus.classList.add('online');
        authStatus.querySelector('.status-text').textContent = '已登录';
        addLog('Telegram 已连接成功', 'success');
        loadChats();
    }

    let allChats = [];
    const chatSearchInput = document.getElementById('chat-search');

    async function loadChats() {
        addLog('正在加载对话列表...', 'info');
        try {
            const response = await fetch('/api/chats');
            const data = await response.json();
            if (data.chats && data.chats.length > 0) {
                allChats = data.chats;
                renderChatSelect(allChats);
                
                // Restore last chat
                if (window.LAST_CHAT_ID) {
                    chatSelect.value = window.LAST_CHAT_ID;
                }
                
                addLog('对话列表加载成功', 'success');
            } else {
                addLog('未找到对话或未登录', 'error');
            }
        } catch (err) {
            addLog('加载对话失败: ' + err.message, 'error');
        }
    }

    function renderChatSelect(chats) {
        if (chats.length === 0) {
            chatSelect.innerHTML = '<option value="">未找到匹配结果</option>';
            return;
        }
        chatSelect.innerHTML = chats.map(chat => 
            `<option value="${chat.id}">${chat.name}</option>`
        ).join('');
    }

    chatSearchInput.addEventListener('input', (e) => {
        const term = e.target.value.toLowerCase();
        const filtered = allChats.filter(chat => 
            chat.name.toLowerCase().includes(term)
        );
        renderChatSelect(filtered);
    });

    // --- Directory Browser Logic ---
    const dirModal = document.getElementById('dir-modal');
    const browseBtn = document.getElementById('browse-folder-btn');
    const closeModal = document.getElementById('close-modal');
    const dirList = document.getElementById('dir-list');
    const currentPathEl = document.getElementById('current-path');
    const selectDirBtn = document.getElementById('select-dir-btn');
    
    let currentBrowsingPath = '';

    browseBtn.addEventListener('click', () => {
        dirModal.classList.remove('hidden');
        loadDirContents(folderPathInput.value.trim() || '');
    });

    closeModal.addEventListener('click', () => dirModal.classList.add('hidden'));

    window.addEventListener('click', (e) => {
        if (e.target === dirModal) dirModal.classList.add('hidden');
    });

    async function loadDirContents(path) {
        dirList.innerHTML = '<div class="hint">正在扫描目录...</div>';
        try {
            const resp = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
            const data = await resp.json();
            
            if (data.status === 'success') {
                currentBrowsingPath = data.current_path;
                currentPathEl.textContent = currentBrowsingPath;
                
                dirList.innerHTML = '';
                data.items.forEach(item => {
                    const div = document.createElement('div');
                    div.className = 'dir-item';
                    const icon = item.name === '..' ? 'corner-left-up' : 'folder';
                    div.innerHTML = `<i data-lucide="${icon}"></i> <span>${item.name}</span>`;
                    div.onclick = () => loadDirContents(item.path);
                    dirList.appendChild(div);
                });
                lucide.createIcons();
            } else {
                dirList.innerHTML = `<div class="log-line error">${data.message}</div>`;
            }
        } catch (err) {
            dirList.innerHTML = `<div class="log-line error">无法访问该目录</div>`;
        }
    }

    selectDirBtn.addEventListener('click', () => {
        folderPathInput.value = currentBrowsingPath;
        dirModal.classList.add('hidden');
    });

    // --- Original Logic ---
    if (!uploadSection.classList.contains('hidden')) {
        authStatus.classList.add('online');
        authStatus.querySelector('.status-text').textContent = '已登录';
        loadChats();
    }

    const refreshChatsBtn = document.getElementById('refresh-chats-btn');
    if (refreshChatsBtn) {
        refreshChatsBtn.addEventListener('click', loadChats);
    }

    // Step 3: Start Upload
    startUploadBtn.addEventListener('click', () => {
        const manualId = document.getElementById('manual-chat-id').value.trim();
        const chatId = manualId || chatSelect.value;
        const folderPath = folderPathInput.value.trim();

        if (!chatId || !folderPath) {
            alert('请选择或输入目标对话，并输入文件夹路径');
            return;
        }

        progressSection.classList.remove('hidden');
        initWebSocket(chatId, folderPath);
    });

    function initWebSocket(chatId, folderPath) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/upload`);

        ws.onopen = () => {
            ws.send(json.stringify({
                action: 'start_upload',
                chat_id: chatId,
                folder_path: folderPath
            }));
            addLog('WebSocket 连接已建立，开始同步...', 'info');
        };

        ws.onmessage = (event) => {
            const data = json.parse(event.data);
            
            if (data.type === 'info') {
                addLog(data.message, 'info');
            } else if (data.type === 'progress') {
                const percent = (data.index / data.total) * 100;
                progressBar.style.width = `${percent}%`;
                progressCount.textContent = `${data.index} / ${data.total}`;
                currentFileName.textContent = data.file;
                
                if (data.status === 'completed') {
                    addLog(`已完成: ${data.file}`, 'success');
                } else {
                    addLog(`正在上传: ${data.file}`, 'progress');
                }
            } else if (data.type === 'error') {
                addLog(data.message, 'error');
            } else if (data.type === 'done') {
                addLog(data.message, 'success');
                progressBar.style.width = '100%';
            }
        };

        ws.onclose = () => {
            addLog('连接已断开', 'info');
        };
    }

    // Helper: JSON wrapper to avoid crashes
    const json = {
        parse: (str) => {
            try { return JSON.parse(str); } catch(e) { return {}; }
        },
        stringify: (obj) => JSON.stringify(obj)
    };

    // Auto Upload Toggle
    const autoUploadToggle = document.getElementById('auto-upload-toggle');
    if (autoUploadToggle) {
        autoUploadToggle.addEventListener('change', async (e) => {
            const enabled = e.target.checked;
            try {
                const formData = new FormData();
                formData.append('enabled', enabled);
                const response = await fetch('/api/config/auto-upload', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                if (data.status === 'success') {
                    addLog(enabled ? '自动同步模式已开启' : '自动同步模式已关闭', enabled ? 'success' : 'info');
                }
            } catch (err) {
                addLog('设置同步失败: ' + err, 'error');
                e.target.checked = !enabled; // Revert
            }
        });
    }
});
