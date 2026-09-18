// liteads.js
// LiteAds JavaScript SDK for Publishers

(function(window, document) {
    'use strict';
    
    var LiteAds = window.LiteAds || {};
    
    /**
     * Renders a LiteAds ad slot into a specified container.
     * 
     * @param {string} slotId - The Publisher Ad Slot ID
     * @param {string} containerId - The ID of the HTML element to render the ad in
     * @param {string} baseUrl - (Optional) The base URL of the LiteAds platform, defaults to current origin
     */
    LiteAds.render = function(slotId, containerId, baseUrl) {
        if (!baseUrl) {
            baseUrl = 'http://localhost:8000'; // Replace with production URL if needed
        }
        
        var container = document.getElementById(containerId);
        if (!container) {
            console.error('LiteAds: Container element not found', containerId);
            return;
        }
        
        var iframe = document.createElement('iframe');
        var encodedSlotId = encodeURIComponent(slotId);
        iframe.src = baseUrl + '/api/v1/ad/serve?slot_id=' + encodedSlotId;
        iframe.style.width = '100%';
        iframe.style.height = '100%';
        iframe.style.border = 'none';
        iframe.style.overflow = 'hidden';
        iframe.scrolling = 'no';
        iframe.title = 'Advertisement';
        
        container.innerHTML = ''; // Clear container
        container.appendChild(iframe);
    };

    window.LiteAds = LiteAds;
})(window, document);
