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

from sugar3.activity import activityfactory

from jarabe.model import bundleregistry
from jarabe.journal.misc import launch
from jarabe.view.socialmap import ACTIVITY_CATEGORY_MAP

_logger = logging.getLogger('Social Help Launched')

SOCIAL_ACTIVITY_BUNDLE_ID = "org.laptop.SocialActivity"

# TODO: Pick it from config
FORUM_URL = "http://localhost:3000/"

def setup_view_social(activity):
    activity_bundle_id = activity.get_bundle_id()
    window_xid = activity.get_xid()
    if window_xid is None:
        _logger.error('Activity without a window xid')
        return

    activity_id = activityfactory.create_activity_id()
    bundle = bundleregistry.get_registry().get_bundle(SOCIAL_ACTIVITY_BUNDLE_ID)
    try:
        uri = FORUM_URL + 'category/' + ACTIVITY_CATEGORY_MAP[activity_bundle_id]
    except KeyError:
        _logger.error('Key Error: Map not found for activity')
    else:
        launch(bundle, activity_id=activity_id, uri=uri)
