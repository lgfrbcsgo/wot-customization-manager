import BigWorld
import BattleReplay
from functools import partial
from helpers import dependency, i18n
from constants import CURRENT_REALM
from CurrentVehicle import g_currentVehicle
from skeletons.gui.shared import IItemsCache
from items.components.c11n_constants import SeasonType
from gui.SystemMessages import SM_TYPE
from gui.customization.service import CustomizationService
from gui.customization.context import CustomizationContext
from gui.shared.items_cache import CACHE_SYNC_REASON
from gui.shared.utils.requesters import REQ_CRITERIA
from gui.shared.utils.decorators import process
from gui.shared.gui_items import GUI_ITEM_TYPE
from gui.shared.gui_items.processors.common import OutfitApplier
from gui.shared.gui_items.customization.outfit import Outfit
from gui.Scaleform.daapi.view.lobby.customization.shared import SEASON_TYPE_TO_IDX, SEASON_IDX_TO_TYPE
from gui.Scaleform.daapi.view.lobby.hangar.TmenXpPanel import TmenXpPanel
from skeletons.gui.game_control import IBootcampController
from skeletons.gui.system_messages import ISystemMessages
from skeletons.gui.customization import ICustomizationService

from ModCustomizationManager.decorators import block_concurrent, run_before, run_before_async
from ModCustomizationManager.cache import Cache
from ModCustomizationManager.frequency_tracker import FrequencyTracker


@dependency.replace_none_kwargs(bootcamp=IBootcampController)
def is_in_bootcamp(bootcamp=None):
    return bootcamp.isInBootcamp()


def get_cache_namespace():
    if BattleReplay.isLoading() or BattleReplay.isPlaying():
        return
    try:
        return BigWorld.player().name + CURRENT_REALM
    except AttributeError:
        pass


def get_applied_outfit_seasons(vehicle):
    def filter_condition(season):
        outfit = vehicle.getOutfit(season)
        if outfit is None:
            return False
        style = vehicle.getStyledOutfit(season)
        style_is_applied = style is not None and style.strCD == outfit.strCD
        return outfit.strCD is not None and not style_is_applied
    return [season for season in SeasonType.COMMON_SEASONS if filter_condition(season)]


# returns a dict from season ID to outfit
def get_applied_outfits(vehicle):
    filtered_seasons = get_applied_outfit_seasons(vehicle)
    return {season: vehicle.getOutfit(season) for season in filtered_seasons}


# returns a dict from season ID to outfit descriptor
def get_applied_outfit_descriptors(vehicle):
    filtered_seasons = get_applied_outfit_seasons(vehicle)
    return {season: vehicle.getOutfit(season).strCD for season in filtered_seasons}


def get_outfits_from_descriptors(outfit_descriptors):
    return {season: Outfit(descriptor) for (season, descriptor) in outfit_descriptors.iteritems()}


def get_descriptors_from_outfits(outfits):
    return {season: outfit.strCD for (season, outfit) in outfits.iteritems()}


# map function over vehicles
def map_vehicles(func, vehicles):
    return {vehicle.intCD: func(vehicle) for vehicle in vehicles}


get_all_applied_outfits = partial(map_vehicles, get_applied_outfits)
get_all_applied_outfit_descriptors = partial(map_vehicles, get_applied_outfit_descriptors)


def get_added_items(current_outfits, new_outfits):
    new_items = []
    for (season, new_outfit) in new_outfits.iteritems():
        current_outfit = current_outfits.get(season, Outfit())
        new_items += list(current_outfit.diff(new_outfit).items())
    return new_items


def count_items_by_id(items):
    count = {}
    for item in items:
        count[item.intCD] = count.get(item.intCD, 0) + 1
    return count


def count_item_type(items, type):
    return reduce(lambda total, item: total + (item.itemTypeID == type), items, 0)


@dependency.replace_none_kwargs(items_cache=IItemsCache)
def get_vehicles(items_cache=None):
    return dict(items_cache.items.getVehicles(REQ_CRITERIA.INVENTORY))


@dependency.replace_none_kwargs(items_cache=IItemsCache)
def get_compatible_items(current_vehicle, items_cache=None):
    return items_cache.items.getItems(
        GUI_ITEM_TYPE.CUSTOMIZATIONS,
        REQ_CRITERIA.CUSTOMIZATION.FOR_VEHICLE(current_vehicle)
    )


def outfits_in_inventory(current_outfits, new_outfits, vehicle):
    available_count = {int_CD: item.fullInventoryCount(vehicle) for (int_CD, item) in get_compatible_items(vehicle).iteritems()}
    required_items = get_added_items(current_outfits, new_outfits)
    required_count = count_items_by_id(required_items)

    for (int_CD, count) in required_count.iteritems():
        if available_count.get(int_CD, 0) < count:
            return False

    return True


cache_instance = Cache('lgfrbcsgo', 'outfitcache.pkl')
frequency_tracker = FrequencyTracker()


# save all applied outfits into cache if cache is empty
def init_cache(namespace):
    cached_outfits = cache_instance.get(namespace, {})
    vehicles = get_vehicles()
    not_indexed_vehicles = {int_CD: vehicle for (int_CD, vehicle) in vehicles.iteritems() if int_CD not in cached_outfits}
    if len(not_indexed_vehicles.keys()) > 0:
        new_cached_outfits = get_all_applied_outfit_descriptors(not_indexed_vehicles.itervalues())
        new_cached_outfits.update(cached_outfits)
        cache_instance.set(new_cached_outfits, namespace)


def init_cache_backup(namespace):
    init_cache(namespace)
    backup_namespace = '%s_backup' % namespace
    if cache_instance.get(backup_namespace) is None:
        init_cache(backup_namespace)


# get required number of this item fot the outfits
# if for_outfits is None return number required to fully equip a vehicle
def get_required_count(item, for_outfits=None, season=SeasonType.SUMMER):
    required_count = 0

    if for_outfits is None:
        if item.itemTypeID == GUI_ITEM_TYPE.PAINT:
            required_count = 5
        elif item.itemTypeID == GUI_ITEM_TYPE.EMBLEM:
            required_count = 2
        elif item.itemTypeID == GUI_ITEM_TYPE.INSCRIPTION:
            required_count = 2
        elif item.itemTypeID == GUI_ITEM_TYPE.MODIFICATION:
            required_count = 1
        elif item.itemTypeID == GUI_ITEM_TYPE.CAMOUFLAGE:
            required_count = 3 if season in item.seasons else 0
    else:
        for outfit in for_outfits.itervalues():
            for required_item in outfit.items():
                if required_item.intCD == item.intCD:
                    required_count += 1

    return required_count


# remove as many outfits as necessary
@block_concurrent
def reclaim(current_vehicle, for_outfits=None, season=SeasonType.SUMMER):
    vehicles = get_vehicles()
    # count customizations in inventory
    available_items = {int_CD: item.fullInventoryCount(current_vehicle) for (int_CD, item) in get_compatible_items(current_vehicle).iteritems()}

    # count customizations on current vehicle
    for (outfit_season, outfit) in get_applied_outfits(current_vehicle).iteritems():
        if for_outfits is None and outfit_season != season:
            continue
        for item in outfit.items():
            available_items[item.intCD] += 1

    reclaim_processors = []

    # determine which outfits to remove
    sorted_vehicles = frequency_tracker.sort_least_frequent(vehicles.values(), getter=lambda vehicle: vehicle.intCD)
    for vehicle in sorted_vehicles:
        outfits = get_applied_outfits(vehicle)

        if vehicle.intCD == current_vehicle.intCD or not vehicle.isAlive:
            continue

        must_demount = False
        for (season, outfit) in outfits.iteritems():
            for item in outfit.items():
                required_amount = get_required_count(item, for_outfits=for_outfits, season=season)
                if item.intCD in available_items and available_items[item.intCD] < required_amount:
                    must_demount = True
                    available_items[item.intCD] += 1

        if must_demount:
            for season in outfits.iterkeys():
                reclaim_processor = OutfitApplier(vehicle, Outfit(), season).request()
                reclaim_processors.append(reclaim_processor)

    if len(reclaim_processors) > 0:
        yield reclaim_processors


@dependency.replace_none_kwargs(customization=ICustomizationService)
def refresh_customization_interface(customization=None):
    context = customization.getCtx()
    season_index = SEASON_TYPE_TO_IDX[context.currentSeason]
    context.changeSeason(season_index)


# removes outfits and applies cached outfits to current vehicle
@process('customizationApply')
@block_concurrent
def swap_customizations(on_vehicle_returning=False):
    namespace = get_cache_namespace()
    init_cache_backup(namespace)

    vehicle = g_currentVehicle.item
    if is_in_bootcamp() or vehicle is None or not vehicle.isAlive:
        return

    frequency_tracker.select(vehicle.intCD)

    cache = cache_instance.get(namespace, {})
    current_outfits = get_applied_outfits(vehicle)
    current_outfits_descr = get_descriptors_from_outfits(current_outfits)
    new_outfits_descr = cache.get(vehicle.intCD, {})

    must_apply = []
    for (season, new_outfit) in new_outfits_descr.iteritems():
        current_outfit = current_outfits_descr.get(season, None)
        if current_outfit != new_outfit:
            must_apply.append(season)

    if len(must_apply) > 0:
        new_outfits = get_outfits_from_descriptors(new_outfits_descr)
        for val in reclaim(vehicle, for_outfits=new_outfits):
            yield val

        system_messages = dependency.instance(ISystemMessages)
        if outfits_in_inventory(get_applied_outfits(vehicle), new_outfits, vehicle):
            # array will never be empty, we're safe
            yield [OutfitApplier(vehicle, outfit, season).request() for (season, outfit) in new_outfits.iteritems() if
                   season in must_apply]
            added_items = get_added_items(current_outfits, new_outfits)
            added_camos = count_item_type(added_items, GUI_ITEM_TYPE.CAMOUFLAGE)
            added_paints = count_item_type(added_items, GUI_ITEM_TYPE.PAINT)
            added_effects = count_item_type(added_items, GUI_ITEM_TYPE.MODIFICATION)

            apply_messages = ['<b>' + vehicle.userName + ':</b>']
            if added_camos > 0:
                apply_messages.append(i18n.makeString('#system_messages:customization/added/camouflageValue', added_camos))
            if added_paints > 0:
                apply_messages.append(i18n.makeString('#system_messages:customization/added/paintValue', added_paints))
            if added_effects > 0:
                apply_messages.append(i18n.makeString('#system_messages:customization/added/modificationValue', added_effects))
            if len(apply_messages) > 1:
                system_messages.pushMessage('\n'.join(apply_messages), SM_TYPE.Information)
        elif not on_vehicle_returning:
            warning_text = '<b>' + vehicle.userName + ':</b>\n' + i18n.makeString('#system_messages:customization/server_error')
            system_messages.pushMessage(warning_text, SM_TYPE.Warning)


# hook into function for detecting vehicle change
# hooking into g_currentVehicle directly breaks when going into battle
# credits to the XVM team for this nice solution
@run_before(TmenXpPanel, '_onVehicleChange')
def on_vehicle_changed(*args, **kwargs):
    swap_customizations()


# hook into the function for opening customization window
@run_before_async(CustomizationService, '_CustomizationService__showCustomization')
def on_before_customization_open(*args, **kwargs):
    for val in reclaim(g_currentVehicle.item, season=SeasonType.SUMMER):
        yield val


@run_before_async(CustomizationContext, 'changeSeason')
def on_before_season_change(_, season_idx, *args, **kwargs):
    for val in reclaim(g_currentVehicle.item, season=SEASON_IDX_TO_TYPE[season_idx]):
        yield val


def on_inventory_changed(reason, diff):
    if diff is None or GUI_ITEM_TYPE.VEHICLE not in diff or reason != CACHE_SYNC_REASON.CLIENT_UPDATE or is_in_bootcamp():
        return

    vehicle = g_currentVehicle.item

    # outfit on current vehicle was changed
    if GUI_ITEM_TYPE.OUTFIT in diff and vehicle is not None and vehicle.intCD in diff[GUI_ITEM_TYPE.VEHICLE]:
        outfits = get_applied_outfit_descriptors(vehicle)
        namespace = get_cache_namespace()
        init_cache_backup(namespace)
        cached_outfits = cache_instance.get(namespace, {})
        cached_outfits[vehicle.intCD] = outfits
        cache_instance.set(cached_outfits, namespace)

    # some vehicle might have returned from battle
    else:
        swap_customizations(on_vehicle_returning=True)


@dependency.replace_none_kwargs(items_cache=IItemsCache)
def init(items_cache=None):
    # register callback on inventory changes
    items_cache.onSyncCompleted += on_inventory_changed


@dependency.replace_none_kwargs(items_cache=IItemsCache)
def fini(items_cache=None):
    # unregister callback on inventory changes
    items_cache.onSyncCompleted -= on_inventory_changed