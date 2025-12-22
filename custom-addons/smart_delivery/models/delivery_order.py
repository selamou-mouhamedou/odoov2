# -*- coding: utf-8 -*-

import math
import random
import string
import logging
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class DeliveryOrder(models.Model):
    _name = 'delivery.order'
    _description = 'Commande de Livraison'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Référence', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    reference = fields.Char(string='Référence Externe', tracking=True)

    def _get_sector_type_selection(self):
        """Dynamic selection based on existing sector.rule records.
        Falls back to the original fixed list if no rules exist."""
        rules = self.env['sector.rule'].search([])
        if not rules:
            return [
                ('standard', 'Standard'),
                ('premium', 'Premium'),
                ('express', 'Express'),
                ('fragile', 'Fragile'),
                ('medical', 'Médical'),
            ]
        return [(r.sector_type, r.sector_type.capitalize()) for r in rules if r.sector_type]

    sector_type = fields.Selection(
        selection=_get_sector_type_selection,
        string='Type de Secteur',
        required=True,
        tracking=True,
    )
    
    @api.onchange('sector_type')
    def _onchange_sector_type(self):
        """Applique automatiquement les règles du secteur sélectionné"""
        if self.sector_type:
            sector_rule = self.env['sector.rule'].search([
                ('sector_type', '=', self.sector_type)
            ], limit=1)
            if sector_rule:
                self.otp_required = sector_rule.otp_required
                self.signature_required = sector_rule.signature_required
                self.photo_required = sector_rule.photo_required
                self.biometric_required = sector_rule.biometric_required
    
    sender_id = fields.Many2one('res.partner', string='Expéditeur', required=True, tracking=True,
                                 domain="[('is_delivery_enterprise', '=', True)]",
                                 help="L'entreprise qui envoie le colis. Seules les entreprises inscrites sont affichées.")
    receiver_name = fields.Char(string='Nom du Destinataire', tracking=True)
    receiver_phone = fields.Char(string='Téléphone Destinataire', required=True, tracking=True)
    
    pickup_lat = fields.Float(string='Latitude Pickup', required=True, digits=(10, 7))
    pickup_long = fields.Float(string='Longitude Pickup', required=True, digits=(10, 7))
    drop_lat = fields.Float(string='Latitude Livraison', required=True, digits=(10, 7))
    drop_long = fields.Float(string='Longitude Livraison', required=True, digits=(10, 7))
    
    assigned_livreur_id = fields.Many2one('delivery.livreur', string='Livreur Assigné', tracking=True,
                                          help="Seuls les livreurs ayant le type de secteur sélectionné sont affichés")
    
    status = fields.Selection([
        ('draft', 'Brouillon'),
        ('dispatching', 'En Cours de Dispatching'),
        ('assigned', 'Assigné'),
        ('on_way', 'En Route'),
        ('delivered', 'Livré'),
        ('failed', 'Échoué'),
        ('cancelled', 'Annulé'),
    ], string='Statut', default='draft', required=True, tracking=True)
    
    # Dispatching fields
    dispatch_batch_size = fields.Integer(string='Taille du Batch', default=10)
    dispatched_livreur_ids = fields.Many2many(
        'delivery.livreur', 
        'delivery_order_dispatched_rel', 
        'order_id', 
        'livreur_id', 
        string='Livreurs Notifiés'
    )
    current_batch_livreur_ids = fields.Many2many(
        'delivery.livreur', 
        'delivery_order_current_batch_rel', 
        'order_id', 
        'livreur_id', 
        string='Batch Actuel'
    )
    dispatch_start_time = fields.Datetime(string='Début du Batch')
    first_dispatch_time = fields.Datetime(string='Début du Dispatching')
    
    # Conditions de validation
    otp_required = fields.Boolean(string='OTP Requis', default=False)
    signature_required = fields.Boolean(string='Signature Requise', default=False)
    photo_required = fields.Boolean(string='Photo Requise', default=False)
    biometric_required = fields.Boolean(string='Biométrie Requise', default=False)
    
    # Relations
    condition_ids = fields.One2many('delivery.condition', 'order_id', string='Conditions')
    route_ids = fields.One2many('delivery.route', 'order_id', string='Itinéraire')
    billing_id = fields.One2many('delivery.billing', 'order_id', string='Facturation')
    
    # Champs calculés
    distance_km = fields.Float(string='Distance (km)', compute='_compute_distance', store=True)
    
    @api.model
    def default_get(self, fields_list):
        """Set default sender_id for enterprise users"""
        defaults = super().default_get(fields_list)
        
        # Check if current user is an enterprise user (not admin)
        user = self.env.user
        admin_group = self.env.ref('smart_delivery.group_admin', raise_if_not_found=False)
        enterprise_group = self.env.ref('smart_delivery.group_enterprise', raise_if_not_found=False)
        
        if enterprise_group and enterprise_group in user.groups_id:
            if not admin_group or admin_group not in user.groups_id:
                # Enterprise user - set default sender_id to their company
                partner = user.partner_id
                company_partner = partner.commercial_partner_id if partner.commercial_partner_id else partner
                defaults['sender_id'] = company_partner.id
        
        return defaults
    
    @api.model_create_multi
    def create(self, vals_list):
        # Check enterprise user permissions
        user = self.env.user
        admin_group = self.env.ref('smart_delivery.group_admin', raise_if_not_found=False)
        enterprise_group = self.env.ref('smart_delivery.group_enterprise', raise_if_not_found=False)
        
        is_enterprise_only = (
            enterprise_group and enterprise_group in user.groups_id and
            (not admin_group or admin_group not in user.groups_id)
        )
        
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('delivery.order') or _('New')
            
            # Enterprise users can only create orders for their own company
            if is_enterprise_only and vals.get('sender_id'):
                partner = user.partner_id
                company_partner_id = partner.commercial_partner_id.id if partner.commercial_partner_id else partner.id
                
                sender = self.env['res.partner'].browse(vals['sender_id'])
                if sender.exists():
                    sender_company_id = sender.commercial_partner_id.id if sender.commercial_partner_id else sender.id
                    if sender_company_id != company_partner_id and sender.parent_id.id != company_partner_id:
                        raise ValidationError(
                            _('Vous ne pouvez créer des commandes que pour votre entreprise: %s') % partner.commercial_partner_id.name
                        )
            
            # Appliquer les règles du secteur
            if vals.get('sector_type'):
                sector_rule = self.env['sector.rule'].search([
                    ('sector_type', '=', vals['sector_type'])
                ], limit=1)
                if sector_rule:
                    vals.setdefault('otp_required', sector_rule.otp_required)
                    vals.setdefault('signature_required', sector_rule.signature_required)
                    vals.setdefault('photo_required', sector_rule.photo_required)
                    vals.setdefault('biometric_required', sector_rule.biometric_required)
        
            # Auto-set status to 'assigned' if a livreur is provided
            if vals.get('assigned_livreur_id'):
                # Check if the livreur is valid (approved and available)
                livreur = self.env['delivery.livreur'].browse(vals['assigned_livreur_id'])
                if livreur.exists() and livreur.registration_status == 'approved' and livreur.availability:
                    vals['status'] = 'assigned'
        
        orders = super().create(vals_list)
        
        # Créer les conditions si nécessaire pour chaque commande
        for order in orders:
            if order.otp_required or order.signature_required or order.photo_required or order.biometric_required:
                condition_vals = {'order_id': order.id}
                # Générer OTP si requis
                if order.otp_required:
                    condition_vals['otp_value'] = ''.join(random.choices(string.digits, k=6))
                self.env['delivery.condition'].create(condition_vals)

            # Auto-assign livreur if none was provided at creation
            if not order.assigned_livreur_id and order.status == 'draft':
                try:
                    order.assign_livreur()
                except UserError:
                    # If no suitable livreur is found, keep the order in draft without failing creation
                    pass
        
        return orders
    
    def write(self, vals):
        """Override write to auto-assign status when livreur is set on draft orders"""
        # If assigning a livreur to a draft order, auto-set status to 'assigned'
        if vals.get('assigned_livreur_id') and 'status' not in vals:
            for order in self:
                if order.status == 'draft':
                    # Check if the livreur is valid
                    livreur = self.env['delivery.livreur'].browse(vals['assigned_livreur_id'])
                    if livreur.exists() and livreur.registration_status == 'approved' and livreur.availability:
                        vals['status'] = 'assigned'
                    break  # Only need to check once
        
        return super().write(vals)
    
    @api.depends('pickup_lat', 'pickup_long', 'drop_lat', 'drop_long')
    def _compute_distance(self):
        for record in self:
            if record.pickup_lat and record.pickup_long and record.drop_lat and record.drop_long:
                record.distance_km = self._haversine_distance(
                    record.pickup_lat, record.pickup_long,
                    record.drop_lat, record.drop_long
                )
            else:
                record.distance_km = 0.0
    
    @staticmethod
    def _haversine_distance(lat1, lon1, lat2, lon2):
        """Calcule la distance en km entre deux points GPS"""
        R = 6371  # Rayon de la Terre en km
        
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def assign_livreur(self, force=False):
        """Lance le processus de dispatching (batch par batch)"""
        self.ensure_one()
        
        # If force=True, we reset the dispatch process
        if force:
            self.write({
                'dispatched_livreur_ids': [(5, 0, 0)],
                'current_batch_livreur_ids': [(5, 0, 0)],
                'status': 'draft',
                'first_dispatch_time': False
            })
        
        if self.status not in ['draft', 'dispatching']:
            raise UserError(_('Seules les commandes en brouillon ou en cours de dispatching peuvent être assignées'))
        
        if self.assigned_livreur_id and not force:
             if self.assigned_livreur_id.availability and self.assigned_livreur_id.verified:
                self.write({'status': 'assigned'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Livreur Confirmé'),
                        'message': _('Le livreur %s déjà assigné a été confirmé') % self.assigned_livreur_id.name,
                        'type': 'success',
                        'sticky': False,
                    }
                }

        # Start dispatching the next batch
        return self._dispatch_next_batch()

    def _dispatch_next_batch(self):
        """Selects the next batch of drivers and notifies them."""
        self.ensure_one()
        
        # 1. Find potential livreurs
        sector_rule = self.env['sector.rule'].search([
            ('sector_type', '=', self.sector_type)
        ], limit=1)

        domain = [
            ('availability', '=', True),
            ('verified', '=', True),
            ('id', 'not in', self.dispatched_livreur_ids.ids) # Exclude already notified
        ]
        if sector_rule:
            domain.append(('sector_ids', 'in', [sector_rule.id]))

        available_livreurs = self.env['delivery.livreur'].search(domain)
        
        if not available_livreurs:
            # No more drivers available
            if not self.dispatched_livreur_ids:
                 raise UserError(_('Aucun livreur disponible pour le moment'))
            else:
                # We have cycled through everyone. 
                # Option: Reset cycle or stay in dispatching?
                # For now, let's keep it in dispatching but log a warning or maybe notify admin?
                # Or just return a warning to the scheduled action
                _logger.info(f"Order {self.id}: cycled through all available drivers.")
                return False

        # 2. Score and sort them
        scored_livreurs = []
        for livreur in available_livreurs:
            distance = self._haversine_distance(
                livreur.current_lat, livreur.current_long,
                self.pickup_lat, self.pickup_long
            )
            # Simple score: closer is better
            score = -distance 
            scored_livreurs.append((score, livreur))
        
        # Sort by score descending (closest first)
        scored_livreurs.sort(key=lambda x: x[0], reverse=True)
        
        # 3. Pick top N (batch size)
        batch_size = self.dispatch_batch_size or 10
        next_batch = [sl[1] for sl in scored_livreurs[:batch_size]]
        
        if not next_batch:
            return False

        # 4. Update order state
        # Clear current batch and add new ones
        batch_ids = [l.id for l in next_batch]
        vals = {
            'status': 'dispatching',
            'current_batch_livreur_ids': [(6, 0, batch_ids)],
            'dispatched_livreur_ids': [(4, l_id) for l_id in batch_ids],
            'dispatch_start_time': fields.Datetime.now()
        }
        if not self.first_dispatch_time:
            vals['first_dispatch_time'] = fields.Datetime.now()
            
        self.write(vals)
        
        # 5. Send Notifications (FCM)
        self._notify_livreurs(next_batch)
        
        return True

    def _notify_livreurs(self, livreurs):
        """Sends FCM notifications to the list of livreurs."""
        from ..utils.firebase_utils import send_push_notification
        
        tokens = [l.fcm_token for l in livreurs if l.fcm_token]
        if not tokens:
            return
            
        title = "Nouvelle Commande Disponible!"
        body = f"Commande {self.name} de {self.sender_id.name}. Distance: {self.distance_km:.1f}km. Secteur: {self.sector_type}"
        data = {
            'order_id': str(self.id),
            'order_name': self.name or '',
            'type': 'new_order',
            'sender_name': self.sender_id.name if self.sender_id else '',
            'receiver_name': self.receiver_name or '',
            'receiver_phone': self.receiver_phone or '',
            'sector_type': self.sector_type or '',
            'distance_km': f"{self.distance_km:.2f}" if self.distance_km else '0.00',
            'pickup_lat': f"{self.pickup_lat:.7f}" if self.pickup_lat else '0.0',
            'pickup_long': f"{self.pickup_long:.7f}" if self.pickup_long else '0.0',
            'drop_lat': f"{self.drop_lat:.7f}" if self.drop_lat else '0.0',
            'drop_long': f"{self.drop_long:.7f}" if self.drop_long else '0.0',
        }
        
        send_push_notification(tokens, title, body, data, env=self.env)

    def _notify_enterprise_assigned(self, livreur):
        """Sends FCM notification to sender when order is assigned."""
        from ..utils.firebase_utils import send_push_notification
        
        if not self.sender_id or not self.sender_id.fcm_token:
            return
            
        tokens = [self.sender_id.fcm_token]
        title = "Commande Acceptée!"
        body = f"Votre commande {self.name} a été acceptée par {livreur.name} ({livreur.phone}). Distance: {self.distance_km:.1f}km"
        data = {
            'order_id': str(self.id),
            'order_name': self.name or '',
            'type': 'order_assigned',
            'livreur_name': livreur.name or '',
            'livreur_phone': livreur.phone or '',
            'receiver_name': self.receiver_name or '',
            'receiver_phone': self.receiver_phone or '',
            'distance_km': f"{self.distance_km:.2f}" if self.distance_km else '0.00',
            'pickup_lat': f"{self.pickup_lat:.7f}" if self.pickup_lat else '0.0',
            'pickup_long': f"{self.pickup_long:.7f}" if self.pickup_long else '0.0',
            'drop_lat': f"{self.drop_lat:.7f}" if self.drop_lat else '0.0',
            'drop_long': f"{self.drop_long:.7f}" if self.drop_long else '0.0',
        }
        
        send_push_notification(tokens, title, body, data, env=self.env)

    def _notify_enterprise_delivered(self):
        """Sends FCM notification to sender when order is delivered."""
        from ..utils.firebase_utils import send_push_notification
        
        if not self.sender_id or not self.sender_id.fcm_token:
            return
            
        tokens = [self.sender_id.fcm_token]
        title = "Commande Livrée!"
        livreur = self.assigned_livreur_id
        livreur_info = f"{livreur.name} ({livreur.phone})" if livreur else "N/A"
        body = f"Votre commande {self.name} a été livrée par {livreur_info}. Distance: {self.distance_km:.1f}km"
        data = {
            'order_id': str(self.id),
            'order_name': self.name or '',
            'type': 'order_delivered',
            'livreur_name': livreur.name if livreur else '',
            'livreur_phone': livreur.phone if livreur else '',
            'receiver_name': self.receiver_name or '',
            'receiver_phone': self.receiver_phone or '',
            'distance_km': f"{self.distance_km:.2f}" if self.distance_km else '0.00',
            'pickup_lat': f"{self.pickup_lat:.7f}" if self.pickup_lat else '0.0',
            'pickup_long': f"{self.pickup_long:.7f}" if self.pickup_long else '0.0',
            'drop_lat': f"{self.drop_lat:.7f}" if self.drop_lat else '0.0',
            'drop_long': f"{self.drop_long:.7f}" if self.drop_long else '0.0',
        }
        
        send_push_notification(tokens, title, body, data, env=self.env)

    def action_accept_delivery(self, livreur_id):
        """Called when a livreur accepts the order via API."""
        self.ensure_one()
        
        livreur = self.env['delivery.livreur'].browse(livreur_id)
        if not livreur.exists():
            return {'error': _('Livreur non trouvé'), 'code': 'LIVREUR_NOT_FOUND'}
            
        if self.status != 'dispatching':
            return {'error': _('Cette commande n\'est plus disponible'), 'code': 'ORDER_NOT_AVAILABLE'}
            
        # Check if this livreur was in the current batch (or previously notified)
        # We allow anyone who was notified to accept, even if batch moved on? 
        # For strict batching, check current_batch_livreur_ids.
        # Let's be lenient: if they were dispatched at all, let them try to take it.
        if livreur.id not in self.dispatched_livreur_ids.ids:
             return {'error': _('Vous n\'êtes pas autorisé à accepter cette commande'), 'code': 'NOT_AUTHORIZED'}

        # Verify availability
        if not livreur.availability or not livreur.verified:
            return {'error': _('Vous n\'êtes pas disponible ou vérifié'), 'code': 'LIVREUR_NOT_AVAILABLE'}

        # Assign!
        self.write({
            'status': 'assigned',
            'assigned_livreur_id': livreur.id,
            'current_batch_livreur_ids': [(5, 0, 0)], # clear batch
        })
        
        # Notify enterprise (sender) about assignment
        self._notify_enterprise_assigned(livreur)
        
        # Notify success
        return {'success': True}

    def process_dispatch_timeout(self):
        """Called by Cron to process timeouts and move to next batch."""
        # Find orders in dispatching state that have timed out
        timeout_seconds = 30
        timeout_threshold = fields.Datetime.now() - timedelta(seconds=timeout_seconds)
        
        orders = self.search([
            ('status', '=', 'dispatching'),
            ('dispatch_start_time', '<', timeout_threshold)
        ])
        
        for order in orders:
            # Check global timeout (3 minutes)
            if order.first_dispatch_time:
                global_timeout = order.first_dispatch_time + timedelta(minutes=3)
                if fields.Datetime.now() > global_timeout:
                    _logger.info(f"Order {order.name} timed out globally (3 mins). Cancelling.")
                    order.action_cancel()
                    continue

            _logger.info(f"Processing timeout for order {order.name}")
            order._dispatch_next_batch()

    
    def validate_conditions(self):
        """Valide toutes les conditions requises pour la livraison"""
        self.ensure_one()
        
        condition = self.condition_ids[:1]
        if not condition:
            raise UserError(_('Aucune condition à valider'))
        
        errors = []
        
        # Valider OTP
        if self.otp_required:
            if not condition.otp_verified:
                errors.append(_('OTP non vérifié'))
        
        # Valider signature
        if self.signature_required:
            if not condition.signature_file:
                errors.append(_('Signature manquante'))
        
        # Valider photo
        if self.photo_required:
            if not condition.photo:
                errors.append(_('Photo manquante'))
        
        # Valider biométrie
        if self.biometric_required:
            if not condition.biometric_score or condition.biometric_score < 0.7:
                errors.append(_('Score biométrique insuffisant (minimum 0.7)'))
        
        if errors:
            raise ValidationError('\n'.join(errors))
        
        condition.write({'validated': True})
        self.write({'status': 'delivered'})
        
        # Notify enterprise (sender) about delivery
        self._notify_enterprise_delivered()
        
        # Générer la facturation
        self._generate_billing()
        
        return True
    
    def _generate_billing(self):
        """Génère la facturation pour la commande basée sur les règles de secteur
        
        Crée un enregistrement delivery.billing et génère automatiquement
        la facture Odoo (account.move) associée.
        """
        self.ensure_one()
        
        if self.billing_id:
            return self.billing_id[0]
        
        # Get pricing from sector rule
        sector_rule = self.env['sector.rule'].search([
            ('sector_type', '=', self.sector_type)
        ], limit=1)
        
        if sector_rule:
            base_tariff = sector_rule.base_price
            distance_fee_per_km = sector_rule.distance_fee_per_km
            free_distance = sector_rule.free_distance_km
        else:
            # Fallback default values
            base_tariff = 50.0
            distance_fee_per_km = 10.0
            free_distance = 5.0
        
        # Calculate distance fees (beyond free distance)
        extra_km = max(0, self.distance_km - free_distance)
        extra_fee = extra_km * distance_fee_per_km
        
        # Create billing record
        billing = self.env['delivery.billing'].create({
            'order_id': self.id,
            'distance_km': self.distance_km,
            'base_tariff': base_tariff,
            'extra_fee': extra_fee,
        })
        
        # Auto-create invoice in Odoo Accounting
        try:
            billing.action_create_invoice()
        except Exception as e:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(f"Could not auto-create invoice for billing {billing.id}: {e}")
        
        return billing
    
    def action_start_delivery(self):
        """Démarre la livraison"""
        self.ensure_one()
        if self.status != 'assigned':
            raise UserError(_('La commande doit être assignée'))
        self.write({'status': 'on_way'})
    
    def action_fail_delivery(self):
        """Marque la livraison comme échouée"""
        self.ensure_one()
        self.write({'status': 'failed'})
    
    def action_cancel(self):
        """Annule la commande - uniquement possible pour les commandes en brouillon, dispatching ou assignées"""
        self.ensure_one()
        if self.status not in ['draft', 'dispatching', 'assigned']:
            raise UserError(_('Seules les commandes en brouillon, en cours de dispatching ou assignées peuvent être annulées'))
        
        # Release the livreur if assigned
        self.write({
            'status': 'cancelled',
            'assigned_livreur_id': False,
        })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Commande Annulée'),
                'message': _('La commande %s a été annulée') % self.name,
                'type': 'warning',
                'sticky': False,
            }
        }
    
    def action_view_conditions(self):
        """Ouvre la vue des conditions de la commande"""
        self.ensure_one()
        return {
            'name': _('Conditions'),
            'type': 'ir.actions.act_window',
            'res_model': 'delivery.condition',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
            'context': {'default_order_id': self.id},
        }
    
    def action_view_billing(self):
        """Ouvre la vue de facturation de la commande"""
        self.ensure_one()
        return {
            'name': _('Facturation'),
            'type': 'ir.actions.act_window',
            'res_model': 'delivery.billing',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
            'context': {'default_order_id': self.id},
        }

