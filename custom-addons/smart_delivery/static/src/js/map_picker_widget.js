/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

/**
 * GPS Map Picker Widget
 * Allows users to select pickup and drop-off points on an interactive map
 */
export class GpsMapPickerWidget extends Component {
    static template = "smart_delivery.GpsMapPickerWidget";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.mapContainer = useRef("mapContainer");
        this.notification = useService("notification");
        
        this.state = useState({
            pickupLat: null,
            pickupLong: null,
            dropLat: null,
            dropLong: null,
            isSelectingPickup: true,
            mapReady: false,
        });

        this.map = null;
        this.pickupMarker = null;
        this.dropMarker = null;
        this.routeLine = null;

        onMounted(() => this.initMap());
        onWillUnmount(() => this.destroyMap());
    }

    /**
     * Get coordinates from record values
     */
    getRecordCoordinates() {
        const record = this.props.record;
        return {
            pickupLat: record.data.pickup_lat || 18.0735, // Nouakchott default
            pickupLong: record.data.pickup_long || -15.9582,
            dropLat: record.data.drop_lat || null,
            dropLong: record.data.drop_long || null,
        };
    }

    /**
     * Initialize Leaflet map
     */
    async initMap() {
        // Load Leaflet CSS if not already loaded
        if (!document.querySelector('link[href*="leaflet"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
            link.integrity = 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';
            link.crossOrigin = '';
            document.head.appendChild(link);
        }

        // Load Leaflet JS if not already loaded
        if (typeof L === 'undefined') {
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
                script.integrity = 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=';
                script.crossOrigin = '';
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
        }

        // Wait a bit for the container to be properly rendered
        await new Promise(resolve => setTimeout(resolve, 100));

        if (!this.mapContainer.el) {
            console.error("Map container not found");
            return;
        }

        const coords = this.getRecordCoordinates();
        
        // Default center: Nouakchott, Mauritanie
        const defaultCenter = [18.0735, -15.9582];
        const center = coords.pickupLat && coords.pickupLong 
            ? [coords.pickupLat, coords.pickupLong] 
            : defaultCenter;

        // Initialize map
        this.map = L.map(this.mapContainer.el, {
            center: center,
            zoom: 13,
            scrollWheelZoom: true,
        });

        // Add OpenStreetMap tile layer
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19,
        }).addTo(this.map);

        // Custom icons
        this.pickupIcon = L.divIcon({
            className: 'custom-pickup-marker',
            html: '<div class="marker-icon pickup"><i class="fa fa-map-marker" style="color: #28a745; font-size: 32px;"></i><span>P</span></div>',
            iconSize: [32, 42],
            iconAnchor: [16, 42],
            popupAnchor: [0, -42],
        });

        this.dropIcon = L.divIcon({
            className: 'custom-drop-marker',
            html: '<div class="marker-icon drop"><i class="fa fa-map-marker" style="color: #dc3545; font-size: 32px;"></i><span>D</span></div>',
            iconSize: [32, 42],
            iconAnchor: [16, 42],
            popupAnchor: [0, -42],
        });

        // Add existing markers if coordinates exist
        if (coords.pickupLat && coords.pickupLong) {
            this.setPickupMarker([coords.pickupLat, coords.pickupLong], false);
            this.state.pickupLat = coords.pickupLat;
            this.state.pickupLong = coords.pickupLong;
        }

        if (coords.dropLat && coords.dropLong) {
            this.setDropMarker([coords.dropLat, coords.dropLong], false);
            this.state.dropLat = coords.dropLat;
            this.state.dropLong = coords.dropLong;
            this.state.isSelectingPickup = false;
        }

        // Draw route if both points exist
        if (this.pickupMarker && this.dropMarker) {
            this.drawRoute();
        }

        // Handle map clicks
        this.map.on('click', (e) => this.onMapClick(e));

        this.state.mapReady = true;
        
        // Force map resize after render
        setTimeout(() => {
            if (this.map) {
                this.map.invalidateSize();
            }
        }, 200);
    }

    /**
     * Destroy map on component unmount
     */
    destroyMap() {
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
    }

    /**
     * Handle map click event
     */
    onMapClick(e) {
        const { lat, lng } = e.latlng;

        if (this.state.isSelectingPickup) {
            this.setPickupMarker([lat, lng], true);
            this.state.pickupLat = lat;
            this.state.pickupLong = lng;
            this.state.isSelectingPickup = false;
            
            this.notification.add("Point de pickup défini! Cliquez sur la carte pour définir le point de livraison.", {
                type: "success",
            });
        } else {
            this.setDropMarker([lat, lng], true);
            this.state.dropLat = lat;
            this.state.dropLong = lng;
            
            this.notification.add("Point de livraison défini!", {
                type: "success",
            });
        }

        // Draw route line if both markers exist
        if (this.pickupMarker && this.dropMarker) {
            this.drawRoute();
        }
    }

    /**
     * Set pickup marker
     */
    setPickupMarker(latlng, updateRecord = false) {
        if (this.pickupMarker) {
            this.pickupMarker.setLatLng(latlng);
        } else {
            this.pickupMarker = L.marker(latlng, { 
                icon: this.pickupIcon,
                draggable: true,
            })
            .addTo(this.map)
            .bindPopup("<b>Point de Pickup</b><br>Glissez pour ajuster")
            .on('dragend', (e) => {
                const newPos = e.target.getLatLng();
                this.state.pickupLat = newPos.lat;
                this.state.pickupLong = newPos.lng;
                this.updateRecordValues();
                if (this.dropMarker) {
                    this.drawRoute();
                }
            });
        }

        if (updateRecord) {
            this.updateRecordValues();
        }
    }

    /**
     * Set drop marker
     */
    setDropMarker(latlng, updateRecord = false) {
        if (this.dropMarker) {
            this.dropMarker.setLatLng(latlng);
        } else {
            this.dropMarker = L.marker(latlng, { 
                icon: this.dropIcon,
                draggable: true,
            })
            .addTo(this.map)
            .bindPopup("<b>Point de Livraison</b><br>Glissez pour ajuster")
            .on('dragend', (e) => {
                const newPos = e.target.getLatLng();
                this.state.dropLat = newPos.lat;
                this.state.dropLong = newPos.lng;
                this.updateRecordValues();
                if (this.pickupMarker) {
                    this.drawRoute();
                }
            });
        }

        if (updateRecord) {
            this.updateRecordValues();
        }
    }

    /**
     * Draw route line between pickup and drop points
     */
    drawRoute() {
        if (this.routeLine) {
            this.map.removeLayer(this.routeLine);
        }

        if (this.pickupMarker && this.dropMarker) {
            const pickupLatLng = this.pickupMarker.getLatLng();
            const dropLatLng = this.dropMarker.getLatLng();

            this.routeLine = L.polyline([pickupLatLng, dropLatLng], {
                color: '#007bff',
                weight: 3,
                opacity: 0.7,
                dashArray: '10, 10',
            }).addTo(this.map);

            // Fit map to show both markers
            const bounds = L.latLngBounds([pickupLatLng, dropLatLng]);
            this.map.fitBounds(bounds, { padding: [50, 50] });
        }
    }

    /**
     * Update the record with new coordinate values
     */
    async updateRecordValues() {
        const record = this.props.record;
        
        if (this.state.pickupLat !== null && this.state.pickupLong !== null) {
            await record.update({
                pickup_lat: this.state.pickupLat,
                pickup_long: this.state.pickupLong,
            });
        }
        
        if (this.state.dropLat !== null && this.state.dropLong !== null) {
            await record.update({
                drop_lat: this.state.dropLat,
                drop_long: this.state.dropLong,
            });
        }
    }

    /**
     * Reset selection to pickup mode
     */
    resetToPickup() {
        this.state.isSelectingPickup = true;
        this.notification.add("Mode sélection pickup activé. Cliquez sur la carte.", {
            type: "info",
        });
    }

    /**
     * Set selection to drop mode
     */
    selectDrop() {
        this.state.isSelectingPickup = false;
        this.notification.add("Mode sélection livraison activé. Cliquez sur la carte.", {
            type: "info",
        });
    }

    /**
     * Clear all markers and reset
     */
    async clearAll() {
        if (this.pickupMarker) {
            this.map.removeLayer(this.pickupMarker);
            this.pickupMarker = null;
        }
        if (this.dropMarker) {
            this.map.removeLayer(this.dropMarker);
            this.dropMarker = null;
        }
        if (this.routeLine) {
            this.map.removeLayer(this.routeLine);
            this.routeLine = null;
        }

        this.state.pickupLat = null;
        this.state.pickupLong = null;
        this.state.dropLat = null;
        this.state.dropLong = null;
        this.state.isSelectingPickup = true;

        // Reset record values
        await this.props.record.update({
            pickup_lat: 0,
            pickup_long: 0,
            drop_lat: 0,
            drop_long: 0,
        });

        this.notification.add("Tous les points ont été effacés.", {
            type: "warning",
        });
    }

    /**
     * Use current location for pickup
     */
    useCurrentLocation() {
        if (!navigator.geolocation) {
            this.notification.add("La géolocalisation n'est pas supportée par votre navigateur.", {
                type: "warning",
            });
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (position) => {
                const { latitude, longitude } = position.coords;
                this.setPickupMarker([latitude, longitude], true);
                this.state.pickupLat = latitude;
                this.state.pickupLong = longitude;
                this.state.isSelectingPickup = false;
                
                this.map.setView([latitude, longitude], 15);
                
                this.notification.add("Position actuelle utilisée comme point de pickup!", {
                    type: "success",
                });

                if (this.dropMarker) {
                    this.drawRoute();
                }
            },
            (error) => {
                this.notification.add("Impossible d'obtenir votre position: " + error.message, {
                    type: "danger",
                });
            },
            { enableHighAccuracy: true }
        );
    }

    /**
     * Get formatted coordinates display
     */
    get pickupCoords() {
        if (this.state.pickupLat && this.state.pickupLong) {
            return `${this.state.pickupLat.toFixed(6)}, ${this.state.pickupLong.toFixed(6)}`;
        }
        return "Non défini";
    }

    get dropCoords() {
        if (this.state.dropLat && this.state.dropLong) {
            return `${this.state.dropLat.toFixed(6)}, ${this.state.dropLong.toFixed(6)}`;
        }
        return "Non défini";
    }

    /**
     * Calculate distance between pickup and drop (Haversine)
     */
    get distanceKm() {
        if (!this.state.pickupLat || !this.state.pickupLong || 
            !this.state.dropLat || !this.state.dropLong) {
            return null;
        }

        const R = 6371; // Earth's radius in km
        const dLat = this.toRad(this.state.dropLat - this.state.pickupLat);
        const dLon = this.toRad(this.state.dropLong - this.state.pickupLong);
        const lat1 = this.toRad(this.state.pickupLat);
        const lat2 = this.toRad(this.state.dropLat);

        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                  Math.sin(dLon / 2) * Math.sin(dLon / 2) * Math.cos(lat1) * Math.cos(lat2);
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        
        return (R * c).toFixed(2);
    }

    toRad(deg) {
        return deg * (Math.PI / 180);
    }
}

// Register the widget
registry.category("fields").add("gps_map_picker", {
    component: GpsMapPickerWidget,
    supportedTypes: ["float"],
});
