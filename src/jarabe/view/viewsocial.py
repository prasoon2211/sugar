# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import logging
from gettext import gettext as _

from gi.repository import Gio

from sugar3.activity import activityfactory

from jarabe.model import bundleregistry
from jarabe.journal.misc import launch
from jarabe.view.socialmap import ACTIVITY_CATEGORY_MAP

logging.debug('Social Help Launched')

SOCIAL_ACTIVITY_BUNDLE_ID = "org.laptop.SocialActivity"

# TODO: Change to finalized global disocurse URL
FORUM_URL = "54.187.40.150"


def setup_view_social(activity_bundle_id):
    activity_id = activityfactory.create_activity_id()
    bundle = bundleregistry.get_registry().\
        get_bundle(SOCIAL_ACTIVITY_BUNDLE_ID)
    settings = Gio.Settings('org.sugarlabs.collaboration')
    social_server = settings.get_string('social-help-server')
    uri = "http://"
    if social_server:
        uri += social_server
    else:
        uri += FORUM_URL

    try:
        uri += '/category/' + ACTIVITY_CATEGORY_MAP[activity_bundle_id]
    except KeyError:
        logging.error('Key Error: Map not found for activity')
    else:
        launch(bundle, activity_id=activity_id, uri=uri)

def check_activity_category(activity_bundle_id):
    return bool(ACTIVITY_CATEGORY_MAP.get(activity_bundle_id, None))
