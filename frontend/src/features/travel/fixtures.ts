// Trips for the Travel page tests: the API's answer on the fake samples (lake,
// ski and beach trips), plus one made-up older trip. No real numbers. Format 1.4:
// the rental car is due Nov 10 and the snorkel tour is a maybe item (in no total).
import type { NextTrip, TripsCheck, TripsResponse } from "../../lib/api";

export const TRIPS: TripsResponse = {
  "today": "2026-10-24",
  "from_year": 2025,
  "source_run_id": "2026-10-16",
  "source_run_date": "2026-10-16",
  "trip_count": 4,
  "tiles": {
    "booked_cents": 71_467,
    "estimate_low_cents": 240_000,
    "estimate_high_cents": 352_000,
    "tbd_count": 1,
    "points": [
      {
        "program_key": "harbor-miles",
        "program_name": "Harbor Miles",
        "points_micro": 50_000_000_000
      }
    ],
    "collect_cents": 15_000,
    "collect_count": 1,
    "pay_cents": 0,
    "pay_count": 0
  },
  "active": [
    {
      "key": "ski-weekend",
      "name": "Ski weekend",
      "status": "booked",
      "start_date": "2027-01-15",
      "end_date": "2027-01-18",
      "month": null,
      "people": [
        {
          "key": "alex",
          "name": "Alex"
        },
        {
          "key": "sam",
          "name": "Sam"
        }
      ],
      "guests": null,
      "vehicle": null,
      "note": "Book the lift tickets in December",
      "estimate_low_cents": 110_000,
      "estimate_high_cents": 160_000,
      "versus_estimate": null,
      "source_run_id": "2026-10-12",
      "source_run_date": "2026-10-12",
      "items": [
        {
          "key": "flights",
          "kind": "flight",
          "label": "Flights",
          "status": "booked",
          "tender": "points",
          "amount_cents": 1120,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": 50_000_000_000,
          "program_key": "harbor-miles",
          "program_name": "Harbor Miles",
          "account_name": "Cobalt card",
          "person_name": "Sam",
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 1120
        },
        {
          "key": "gear",
          "kind": "shopping",
          "label": "Winter gear on the store plan",
          "status": "booked",
          "tender": "cash",
          "amount_cents": 18_000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": "Bramble store card",
          "person_name": "Sam",
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 18_000
        },
        {
          "key": "hotel",
          "kind": "lodging",
          "label": "Mountain inn",
          "status": "booked",
          "tender": "free_night",
          "amount_cents": 0,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": "lantern-hotels",
          "program_name": "Lantern Hotels",
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "lift-tickets",
          "kind": "tickets",
          "label": "Lift tickets",
          "status": "estimate",
          "tender": "cash",
          "amount_cents": null,
          "estimate_low_cents": 30_000,
          "estimate_high_cents": 42_000,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "parking",
          "kind": "parking",
          "label": "Resort parking",
          "status": "tbd",
          "tender": "cash",
          "amount_cents": null,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "points-credit",
          "kind": "other",
          "label": "Points toward the inn fees",
          "status": "posted",
          "tender": "points_credit",
          "amount_cents": 4000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": "summit-points",
          "program_name": "Summit Points",
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": -4000
        },
        {
          "key": "rental-car",
          "kind": "rental_car",
          "label": "Rental car",
          "status": "booked",
          "tender": "cash",
          "amount_cents": 30_000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": "Summit card",
          "person_name": "Alex",
          "note": null,
          "due_on": "2026-11-10",
          "maybe": false,
          "cash_cents": 30_000
        }
      ],
      "ious": [
        {
          "key": "car-share",
          "direction": "owed_to_us",
          "counterparty": "friend couple",
          "amount_cents": 15_000,
          "status": "open",
          "settled_on": null,
          "item_key": "rental-car",
          "item_label": "Rental car",
          "note": "Their half of the rental car"
        }
      ],
      "totals": {
        "cash_cents": 45_120,
        "estimate_low_cents": 30_000,
        "estimate_high_cents": 42_000,
        "tbd_count": 1,
        "owed_to_us_cents": 15_000,
        "we_owe_cents": 0,
        "our_net_cents": 30_120,
        "points": [
          {
            "program_key": "harbor-miles",
            "program_name": "Harbor Miles",
            "points_micro": 50_000_000_000
          }
        ],
        "by_kind": {
          "flight": 1120,
          "lodging": 0,
          "food": 0,
          "other": 44_000
        }
      },
      "tagged": {
        "row_count": 0,
        "out_cents": 0,
        "posted_cents": -4000,
        "out_differs": true,
        "deposits_cents": 0,
        "settled_to_us_cents": 0,
        "deposits_differ": false
      }
    },
    {
      "key": "beach-week",
      "name": "Beach week",
      "status": "planning",
      "start_date": null,
      "end_date": null,
      "month": "2027-06",
      "people": [
        {
          "key": "alex",
          "name": "Alex"
        },
        {
          "key": "jamie",
          "name": "Jamie"
        },
        {
          "key": "riley",
          "name": "Riley"
        },
        {
          "key": "sam",
          "name": "Sam"
        }
      ],
      "guests": null,
      "vehicle": null,
      "note": null,
      "estimate_low_cents": 240_000,
      "estimate_high_cents": 360_000,
      "versus_estimate": null,
      "source_run_id": "2026-10-12",
      "source_run_date": "2026-10-12",
      "items": [
        {
          "key": "flights",
          "kind": "flight",
          "label": "Flights",
          "status": "estimate",
          "tender": "cash",
          "amount_cents": null,
          "estimate_low_cents": 90_000,
          "estimate_high_cents": 130_000,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "rental",
          "kind": "lodging",
          "label": "Beach rental",
          "status": "estimate",
          "tender": "cash",
          "amount_cents": null,
          "estimate_low_cents": 120_000,
          "estimate_high_cents": 180_000,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "snorkel-tour",
          "kind": "activities",
          "label": "Snorkel tour",
          "status": "estimate",
          "tender": "cash",
          "amount_cents": null,
          "estimate_low_cents": 15_000,
          "estimate_high_cents": 25_000,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": true,
          "cash_cents": 0
        }
      ],
      "ious": [],
      "totals": {
        "cash_cents": 0,
        "estimate_low_cents": 210_000,
        "estimate_high_cents": 310_000,
        "tbd_count": 0,
        "owed_to_us_cents": 0,
        "we_owe_cents": 0,
        "our_net_cents": 0,
        "points": [],
        "by_kind": {
          "flight": 0,
          "lodging": 0,
          "food": 0,
          "other": 0
        }
      },
      "tagged": {
        "row_count": 0,
        "out_cents": 0,
        "posted_cents": 0,
        "out_differs": false,
        "deposits_cents": 0,
        "settled_to_us_cents": 0,
        "deposits_differ": false
      }
    }
  ],
  "open_items": {
    "collect": [
      {
        "key": "car-share",
        "direction": "owed_to_us",
        "counterparty": "friend couple",
        "amount_cents": 15_000,
        "status": "open",
        "settled_on": null,
        "item_key": "rental-car",
        "item_label": "Rental car",
        "note": "Their half of the rental car",
        "trip_key": "ski-weekend",
        "trip_name": "Ski weekend"
      }
    ],
    "pay": [],
    "due": [
      {
        "key": "rental-car",
        "kind": "rental_car",
        "label": "Rental car",
        "status": "booked",
        "tender": "cash",
        "amount_cents": 30_000,
        "estimate_low_cents": null,
        "estimate_high_cents": null,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": "Summit card",
        "person_name": "Alex",
        "note": null,
        "due_on": "2026-11-10",
        "maybe": false,
        "cash_cents": 30_000,
        "trip_key": "ski-weekend",
        "trip_name": "Ski weekend"
      }
    ],
    "items": [
      {
        "key": "lift-tickets",
        "kind": "tickets",
        "label": "Lift tickets",
        "status": "estimate",
        "tender": "cash",
        "amount_cents": null,
        "estimate_low_cents": 30_000,
        "estimate_high_cents": 42_000,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": null,
        "person_name": null,
        "note": null,
        "due_on": null,
        "maybe": false,
        "cash_cents": 0,
        "trip_key": "ski-weekend",
        "trip_name": "Ski weekend"
      },
      {
        "key": "parking",
        "kind": "parking",
        "label": "Resort parking",
        "status": "tbd",
        "tender": "cash",
        "amount_cents": null,
        "estimate_low_cents": null,
        "estimate_high_cents": null,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": null,
        "person_name": null,
        "note": null,
        "due_on": null,
        "maybe": false,
        "cash_cents": 0,
        "trip_key": "ski-weekend",
        "trip_name": "Ski weekend"
      },
      {
        "key": "flights",
        "kind": "flight",
        "label": "Flights",
        "status": "estimate",
        "tender": "cash",
        "amount_cents": null,
        "estimate_low_cents": 90_000,
        "estimate_high_cents": 130_000,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": null,
        "person_name": null,
        "note": null,
        "due_on": null,
        "maybe": false,
        "cash_cents": 0,
        "trip_key": "beach-week",
        "trip_name": "Beach week"
      },
      {
        "key": "rental",
        "kind": "lodging",
        "label": "Beach rental",
        "status": "estimate",
        "tender": "cash",
        "amount_cents": null,
        "estimate_low_cents": 120_000,
        "estimate_high_cents": 180_000,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": null,
        "person_name": null,
        "note": null,
        "due_on": null,
        "maybe": false,
        "cash_cents": 0,
        "trip_key": "beach-week",
        "trip_name": "Beach week"
      },
      {
        "key": "snorkel-tour",
        "kind": "activities",
        "label": "Snorkel tour",
        "status": "estimate",
        "tender": "cash",
        "amount_cents": null,
        "estimate_low_cents": 15_000,
        "estimate_high_cents": 25_000,
        "points_micro": null,
        "program_key": null,
        "program_name": null,
        "account_name": null,
        "person_name": null,
        "note": null,
        "due_on": null,
        "maybe": true,
        "cash_cents": 0,
        "trip_key": "beach-week",
        "trip_name": "Beach week"
      }
    ]
  },
  "summary": {
    "rows": [
      {
        "key": "lake-weekend",
        "name": "Lake weekend",
        "status": "final",
        "start_date": "2026-07-03",
        "end_date": "2026-07-06",
        "month": null,
        "people": [
          {
            "key": "alex",
            "name": "Alex"
          },
          {
            "key": "jamie",
            "name": "Jamie"
          },
          {
            "key": "riley",
            "name": "Riley"
          },
          {
            "key": "sam",
            "name": "Sam"
          }
        ],
        "guests": 2,
        "total_cents": 26_347,
        "estimate_low_cents": 0,
        "estimate_high_cents": 0,
        "tbd_count": 0,
        "flight_cents": 0,
        "lodging_cents": 21_000,
        "food_cents": 0,
        "other_cents": 5347
      },
      {
        "key": "ski-weekend",
        "name": "Ski weekend",
        "status": "booked",
        "start_date": "2027-01-15",
        "end_date": "2027-01-18",
        "month": null,
        "people": [
          {
            "key": "alex",
            "name": "Alex"
          },
          {
            "key": "sam",
            "name": "Sam"
          }
        ],
        "guests": null,
        "total_cents": 45_120,
        "estimate_low_cents": 30_000,
        "estimate_high_cents": 42_000,
        "tbd_count": 1,
        "flight_cents": 1120,
        "lodging_cents": 0,
        "food_cents": 0,
        "other_cents": 44_000
      },
      {
        "key": "beach-week",
        "name": "Beach week",
        "status": "planning",
        "start_date": null,
        "end_date": null,
        "month": "2027-06",
        "people": [
          {
            "key": "alex",
            "name": "Alex"
          },
          {
            "key": "jamie",
            "name": "Jamie"
          },
          {
            "key": "riley",
            "name": "Riley"
          },
          {
            "key": "sam",
            "name": "Sam"
          }
        ],
        "guests": null,
        "total_cents": 0,
        "estimate_low_cents": 210_000,
        "estimate_high_cents": 310_000,
        "tbd_count": 0,
        "flight_cents": 0,
        "lodging_cents": 0,
        "food_cents": 0,
        "other_cents": 0
      }
    ],
    "booked_cents": 71_467,
    "estimate_low_cents": 240_000,
    "estimate_high_cents": 352_000,
    "tbd_count": 1
  },
  "completed": [
    {
      "key": "lake-weekend",
      "name": "Lake weekend",
      "status": "final",
      "start_date": "2026-07-03",
      "end_date": "2026-07-06",
      "month": null,
      "people": [
        {
          "key": "alex",
          "name": "Alex"
        },
        {
          "key": "jamie",
          "name": "Jamie"
        },
        {
          "key": "riley",
          "name": "Riley"
        },
        {
          "key": "sam",
          "name": "Sam"
        }
      ],
      "guests": 2,
      "vehicle": "Family car",
      "note": "All costs are on the card statements",
      "estimate_low_cents": 45_000,
      "estimate_high_cents": 60_000,
      "versus_estimate": "under",
      "source_run_id": "2026-10-06-2",
      "source_run_date": "2026-10-06",
      "items": [
        {
          "key": "boat",
          "kind": "activities",
          "label": "Boat rental",
          "status": "posted",
          "tender": "covered",
          "amount_cents": 12_000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": "The relatives paid",
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        },
        {
          "key": "gas",
          "kind": "gas",
          "label": "Gas on the way home",
          "status": "posted",
          "tender": "cash",
          "amount_cents": 5347,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": "Summit card",
          "person_name": "Alex",
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 5347
        },
        {
          "key": "lodging",
          "kind": "lodging",
          "label": "Lakeside Lodge",
          "status": "posted",
          "tender": "cash",
          "amount_cents": 21_000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": "Summit card",
          "person_name": "Alex",
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 21_000
        },
        {
          "key": "souvenirs",
          "kind": "shopping",
          "label": "Souvenirs for a friend",
          "status": "excluded",
          "tender": "cash",
          "amount_cents": 3500,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 0
        }
      ],
      "ious": [
        {
          "key": "gas-share",
          "direction": "owed_to_us",
          "counterparty": "relative",
          "amount_cents": 2500,
          "status": "waived",
          "settled_on": null,
          "item_key": "gas",
          "item_label": "Gas on the way home",
          "note": "Called it a wash"
        }
      ],
      "totals": {
        "cash_cents": 26_347,
        "estimate_low_cents": 0,
        "estimate_high_cents": 0,
        "tbd_count": 0,
        "owed_to_us_cents": 0,
        "we_owe_cents": 0,
        "our_net_cents": 26_347,
        "points": [],
        "by_kind": {
          "flight": 0,
          "lodging": 21_000,
          "food": 0,
          "other": 5347
        }
      },
      "tagged": {
        "row_count": 2,
        "out_cents": 26_347,
        "posted_cents": 26_347,
        "out_differs": false,
        "deposits_cents": 0,
        "settled_to_us_cents": 0,
        "deposits_differ": false
      }
    }
  ],
  "older": [
    {
      "key": "hills-jun24",
      "name": "Hills trip",
      "status": "final",
      "start_date": "2024-06-01",
      "end_date": "2024-06-03",
      "month": null,
      "people": [
        {
          "key": "alex",
          "name": "Alex"
        }
      ],
      "guests": null,
      "vehicle": null,
      "note": null,
      "estimate_low_cents": null,
      "estimate_high_cents": null,
      "versus_estimate": null,
      "source_run_id": "2026-10-16",
      "source_run_date": "2026-10-16",
      "items": [
        {
          "key": "cabin",
          "kind": "lodging",
          "label": "Cabin",
          "status": "posted",
          "tender": "cash",
          "amount_cents": 40_000,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 40_000
        },
        {
          "key": "food",
          "kind": "food",
          "label": "Food",
          "status": "posted",
          "tender": "cash",
          "amount_cents": 6215,
          "estimate_low_cents": null,
          "estimate_high_cents": null,
          "points_micro": null,
          "program_key": null,
          "program_name": null,
          "account_name": null,
          "person_name": null,
          "note": null,
          "due_on": null,
          "maybe": false,
          "cash_cents": 6215
        }
      ],
      "ious": [
        {
          "key": "dinner",
          "direction": "we_owe",
          "counterparty": "relative",
          "amount_cents": 4000,
          "status": "open",
          "settled_on": null,
          "item_key": null,
          "item_label": null,
          "note": null
        }
      ],
      "totals": {
        "cash_cents": 46_215,
        "estimate_low_cents": 0,
        "estimate_high_cents": 0,
        "tbd_count": 0,
        "owed_to_us_cents": 0,
        "we_owe_cents": 4000,
        "our_net_cents": 50_215,
        "points": [],
        "by_kind": {
          "flight": 0,
          "lodging": 40_000,
          "food": 6215,
          "other": 0
        }
      },
      "tagged": {
        "row_count": 0,
        "out_cents": 0,
        "posted_cents": 46_215,
        "out_differs": true,
        "deposits_cents": 0,
        "settled_to_us_cents": 0,
        "deposits_differ": false
      }
    }
  ]
};

export const NO_TRIPS: TripsResponse = {
  "today": "2026-10-24",
  "from_year": 2025,
  "source_run_id": null,
  "source_run_date": null,
  "trip_count": 0,
  "tiles": {
    "booked_cents": 0,
    "estimate_low_cents": 0,
    "estimate_high_cents": 0,
    "tbd_count": 0,
    "points": [],
    "collect_cents": 0,
    "collect_count": 0,
    "pay_cents": 0,
    "pay_count": 0
  },
  "active": [],
  "open_items": {
    "collect": [],
    "pay": [],
    "due": [],
    "items": []
  },
  "summary": {
    "rows": [],
    "booked_cents": 0,
    "estimate_low_cents": 0,
    "estimate_high_cents": 0,
    "tbd_count": 0
  },
  "completed": [],
  "older": []
};

export const NEXT_TRIP: NextTrip = {
  "key": "ski-weekend",
  "name": "Ski weekend",
  "status": "booked",
  "start_date": "2027-01-15",
  "month": null,
  "cash_cents": 45_120,
  "estimate_low_cents": 30_000,
  "estimate_high_cents": 42_000,
  "tbd_count": 1
};

export const TRIPS_CHECK: TripsCheck = {
  "trip_count": 4,
  "unknown": [
    {
      "key": "day-trip-sep26",
      "status": "not_sent",
      "rows": 1,
      "plans": 0,
      "flags": 0,
      "redemptions": 0
    }
  ],
  "tagged": [
    {
      "row_count": 0,
      "out_cents": 0,
      "posted_cents": 46_215,
      "out_differs": true,
      "deposits_cents": 0,
      "settled_to_us_cents": 0,
      "deposits_differ": false,
      "key": "hills-jun24",
      "name": "Hills trip"
    },
    {
      "row_count": 2,
      "out_cents": 26_347,
      "posted_cents": 26_347,
      "out_differs": false,
      "deposits_cents": 0,
      "settled_to_us_cents": 0,
      "deposits_differ": false,
      "key": "lake-weekend",
      "name": "Lake weekend"
    },
    {
      "row_count": 0,
      "out_cents": 0,
      "posted_cents": -4000,
      "out_differs": true,
      "deposits_cents": 0,
      "settled_to_us_cents": 0,
      "deposits_differ": false,
      "key": "ski-weekend",
      "name": "Ski weekend"
    }
  ]
};
