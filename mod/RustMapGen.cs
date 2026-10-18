// RustMapGen: Harmony patches that turn a plain Rust dedicated server into a map generator.
// Never install this file on a server people play on: a server that loads it refuses to open its
// network and quits.
//
// What it does, driven by the job file named in RUSTMAPGEN_JOB (written by rustmapgen/recipe.py):
//   - shapes the land right after the game has made its height map (share of land, heights,
//     shallows), which the game's own settings cannot do;
//   - lowers or raises the world size the rail ring and the road ring ask for;
//   - gives wanted monuments the game's highest placement priority and lets them stand on any
//     world size; sets counts and distances per placement step;
//   - pulls the search for one monument towards the middle of the map;
//   - writes down what the game would have done by itself (census.json) and the game's prefab
//     table (prefabs.json);
//   - when the map has been saved, writes report.json and quits.
// The game never reaches the point where it listens for players, tells Steam about itself or
// uploads the map anywhere.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using System.Threading.Tasks;
using HarmonyLib;
using Newtonsoft.Json;
using Unity.Collections;
using UnityEngine;

namespace RustMapGen
{
	public sealed class Job
	{
		public string Out = string.Empty;
		// -1 leaves the game's own value.
		public int RingMinWorldSize = -1;
		public int RailsideMinWorldSize = -1;
		// Part of a monument's prefab path -> the smallest world it is allowed on.
		public Dictionary<string, int> MonumentMinWorldSize = new Dictionary<string, int>();
		// Part of a monument's prefab path -> its placement priority (Lowest, Low, Default, High, Highest).
		// The game places every Highest monument first and relaxes its distance rules for it.
		// "First" is one step above Highest: that monument is tried before all the others.
		public Dictionary<string, string> MonumentPriority = new Dictionary<string, string>();
		// Part of the prefab path of the monument that goes to the middle of the map ("" = none).
		// Its candidate places start within CentreStart metres of the middle and spread by CentreGrow
		// metres with every attempt, so it lands on the valid place nearest to the middle.
		public string CentreMonument = string.Empty;
		public float CentreStart = 4f;
		public float CentreGrow = 0.35f;
		// The monument in the middle may stand in any tier. Without it the search runs outward until
		// it meets the tier the game gives that monument, which can be hundreds of metres away.
		public bool CentreAnyTier;
		// A placement step's resource folder -> what it is told instead of the game's own values.
		public Dictionary<string, StepRule> Steps = new Dictionary<string, StepRule>();
		// Every step's distances between monuments are multiplied by this (below 1: closer together).
		public float RelaxDistances = 1f;
		public int RoadRingMinWorldSize = -1;
		// The shape of the land, applied to the height map right after the game has made it and
		// before anything is placed on it. CoastLift: metres the shallow sea floor is raised by, which
		// moves the shore outwards; it fades out over CoastFade metres of depth. HeightScale: what
		// every height above the new shore is multiplied by (below 1 flattens hills and mountains).
		public float CoastLift;
		public float CoastFade = 25f;
		public float HeightScale = 1f;
		// Above 0: the share of the square that is to be land; the shore is moved to make it so, and
		// CoastLift is not read.
		public float LandShare;
		// Five heights in metres, or none: where the land is to stand at the level 10, 50, 90 and 99 %
		// of it lies below, and at its top. HeightScale is not read when this is given.
		public float[] HeightProfile = new float[0];
		// The shallows, or none: SeaShares[i] of the square is to be sea no deeper than SeaDepths[i]
		// metres (shares counted from the shore outwards, each larger than the one before). Beyond the
		// last depth the sea floor returns to the game's own by CoastFade metres of depth.
		public float[] SeaShares = new float[0];
		public float[] SeaDepths = new float[0];
		// Above 0: the heights are bent on the land as it looks from afar (smoothed over this many
		// metres), and what stands out against that (ridges, slopes, cliffs) is put back times
		// ReliefKeep. With 0 every height is bent alone, which also flattens the slopes.
		public float ReliefRadius;
		public float ReliefKeep = 1f;
		// Above 0: the land within this many metres of the middle of the map is levelled (fully at the
		// middle, not at all at the rim), so that the monument wanted there finds flat ground.
		public float MiddlePlateau;
		// 0 to 1, or below 0 for the game's own: the share of tier 0 that becomes jungle. The game's
		// world config file cannot set it (WorldConfig.LoadFromWorldConfig copies every other share
		// and leaves this one at its default), so it is set here, just before the biomes are made.
		public float BiomeJungle = -1f;
		// A load test: nothing is generated. The game loads the map it was started with, and what
		// it loaded is written down when it reaches the network step.
		public bool LoadTest;
	}

	// -1 leaves the game's own value.
	public sealed class StepRule
	{
		public int Count = -1;
		public int MinWorldSize = -1;
		public int DistanceSame = -1;
		public int DistanceOther = -1;
	}

	public static class State
	{
		public static readonly Job Job;
		public static readonly DateTime Started = DateTime.UtcNow;
		public static readonly List<Dictionary<string, object>> Steps = new List<Dictionary<string, object>>();
		public static readonly Dictionary<string, Dictionary<string, object>> Monuments = new Dictionary<string, Dictionary<string, object>>();
		public static readonly List<string> Notes = new List<string>();

		public static bool Active => Job != null;

		static State()
		{
			string path = Environment.GetEnvironmentVariable("RUSTMAPGEN_JOB");
			if (string.IsNullOrEmpty(path))
			{
				Log("no RUSTMAPGEN_JOB: nothing will be changed, and the network stays closed");
				return;
			}
			try
			{
				Job = JsonConvert.DeserializeObject<Job>(File.ReadAllText(path));
				if (Job.CentreMonument.Contains("monument/roadside/") || Job.CentreMonument.Contains("monument/offshore/"))
				{
					throw new InvalidOperationException("roadside/offshore centre placement is unsupported");
				}
				Directory.CreateDirectory(Job.Out);
				Log("job " + path + " -> " + Job.Out);
			}
			catch (Exception error)
			{
				Job = null;
				Log("job file not read: " + error.Message);
			}
		}

		public static void Log(string text)
		{
			Debug.Log("[RustMapGen] " + text);
		}

		public static void Note(string text)
		{
			Notes.Add(text);
			Log(text);
		}

		public static void Write(string name, object value)
		{
			File.WriteAllText(Path.Combine(Job.Out, name), JsonConvert.SerializeObject(value, Formatting.Indented));
		}

		// The biomes a placement may stand in, from its flag bits (-1: any), by the game's names.
		public static object BiomeNames(int mask)
		{
			string[] names = { "Arid", "Temperate", "Tundra", "Arctic", "Jungle" };
			if ((mask & 31) == 31)
			{
				return "any";
			}
			return names.Where((name, index) => (mask & (1 << index)) != 0).ToArray();
		}

		// The game's own setting of every monument of a placement step, then the job's. The biomes a
		// monument may stand in are those every terrain filter of its prefab and the step's own allow.
		public static void Monument(string step, string resourceFolder, SpawnFilter stepFilter = null)
		{
			if (string.IsNullOrWhiteSpace(resourceFolder))
			{
				return;
			}
			foreach (string folder in resourceFolder.Split(','))
			{
				Prefab<MonumentInfo>[] prefabs = Prefab.Load<MonumentInfo>("assets/bundled/prefabs/autospawn/" + folder + "/", null, null, useProbabilities: false, useWorldConfig: false);
				foreach (Prefab<MonumentInfo> prefab in prefabs)
				{
					MonumentInfo info = prefab.Component;
					if (info == null)
					{
						continue;
					}
					if (!Monuments.TryGetValue(prefab.Name, out Dictionary<string, object> row))
					{
						row = new Dictionary<string, object>
						{
							["prefab"] = prefab.Name,
							["step"] = step,
							["game_min_world_size"] = info.MinWorldSize,
							["game_priority"] = prefab.Parameters != null ? prefab.Parameters.Priority.ToString() : "none",
							["box_m"] = new[] { Math.Round(info.Bounds.size.x, 1), Math.Round(info.Bounds.size.z, 1) },
							["tier"] = info.Tier.ToString(),
							["type"] = info.Type.ToString(),
							["allowed"] = World.Config.IsPrefabAllowed(prefab.Name),
						};
						try
						{
							int biomes = stepFilter != null ? (int)stepFilter.BiomeType : -1;
							foreach (TerrainFilter filter in (prefab.Attribute ?? PrefabAttribute.server).FindAll<TerrainFilter>(prefab.ID))
							{
								if (filter.Filter != null)
								{
									biomes &= (int)filter.Filter.BiomeType;
								}
							}
							row["biomes"] = BiomeNames(biomes);
						}
						catch (Exception error)
						{
							row["biomes_error"] = error.Message;
						}
						Monuments[prefab.Name] = row;
					}
					if (Job.CentreAnyTier && Job.CentreMonument.Length > 0 && prefab.Name.Contains(Job.CentreMonument) && (int)info.Tier != -1)
					{
						info.Tier = (MonumentTier)(-1);
						row["tier_used"] = "any";
					}
					foreach (KeyValuePair<string, string> rule in Job.MonumentPriority)
					{
						if (!prefab.Name.Contains(rule.Key))
						{
							continue;
						}
						PrefabPriority wanted;
						if (rule.Value == "First")
						{
							wanted = PrefabPriority.Highest + 1;
						}
						else if (!Enum.TryParse(rule.Value, out wanted))
						{
							continue;
						}
						if (prefab.Parameters == null)
						{
							// The game reads the priority from this component; a prefab without one gets one.
							prefab.Parameters = prefab.Object.AddComponent<PrefabParameters>();
						}
						if (prefab.Parameters.Priority != wanted)
						{
							prefab.Parameters.Priority = wanted;
							row["priority"] = rule.Value;
						}
					}
					foreach (KeyValuePair<string, int> rule in Job.MonumentMinWorldSize)
					{
						if (prefab.Name.Contains(rule.Key) && info.MinWorldSize != rule.Value)
						{
							info.MinWorldSize = rule.Value;
							row["min_world_size"] = rule.Value;
						}
					}
				}
			}
		}

		// Offshore placement gates the entire step rather than reading each monument's MinWorldSize.
		public static bool WantsFolder(string resourceFolder)
		{
			return (resourceFolder ?? string.Empty).Split(',').Any(folder => folder.Length > 0 &&
				Job.MonumentMinWorldSize.Keys.Any(fragment => fragment.StartsWith(folder + "/", StringComparison.Ordinal)));
		}

		// The rule a recipe gives a placement step: its own folder first, else a folder it holds.
		public static StepRule Rule(string resourceFolder)
		{
			if (string.IsNullOrEmpty(resourceFolder))
			{
				return null;
			}
			if (Job.Steps.TryGetValue(resourceFolder, out StepRule exact))
			{
				return exact;
			}
			foreach (KeyValuePair<string, StepRule> rule in Job.Steps)
			{
				if (resourceFolder.Split(',').Contains(rule.Key))
				{
					return rule.Value;
				}
			}
			return null;
		}

		// Every prefab the game knows, by the number a map file stores for it: the table the map
		// reader names prefabs with. Read from the game's own string pool, so it is always the
		// table of the build that made the map.
		public static void WritePrefabTable()
		{
			try
			{
				Dictionary<uint, string> pool = AccessTools.Field(typeof(StringPool), "toString").GetValue(null) as Dictionary<uint, string>;
				if (pool == null)
				{
					Note("prefab table not read: the string pool is empty");
					return;
				}
				SortedDictionary<string, string> table = new SortedDictionary<string, string>(StringComparer.Ordinal);
				foreach (KeyValuePair<uint, string> entry in pool)
				{
					if (entry.Value != null && entry.Value.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
					{
						table[entry.Key.ToString()] = entry.Value;
					}
				}
				File.WriteAllText(Path.Combine(Job.Out, "prefabs.json"), JsonConvert.SerializeObject(table));
			}
			catch (Exception error)
			{
				Note("prefab table not read: " + error.Message);
			}
		}

		// The game has loaded a map from a file and spawned it: what it holds, for a load test.
		public static void Loaded()
		{
			try
			{
				List<MonumentInfo> monuments = AccessTools.Field(typeof(TerrainPath), "Monuments").GetValue(TerrainMeta.Path) as List<MonumentInfo>;
				WorldSerialization file = World.Serialization;
				Write("loaded.json", new Dictionary<string, object>
				{
					["url"] = World.Url,
					["size"] = World.Size,
					["cached"] = World.Cached,
					["checksum"] = World.Checksum,
					["prefabs"] = file.world.prefabs.Count,
					["paths"] = file.world.paths.Count,
					["monuments"] = monuments != null ? monuments.Count : -1,
					["entities"] = BaseNetworkable.serverEntities.Count,
					["seconds"] = Math.Round((DateTime.UtcNow - Started).TotalSeconds, 1),
				});
				File.WriteAllText(Path.Combine(Job.Out, "done"), "loaded\n");
			}
			catch (Exception error)
			{
				File.WriteAllText(Path.Combine(Job.Out, "failed"), "the loaded map could not be described: " + error.Message + "\n");
			}
		}

		public static void Finish(string fileName)
		{
			// A newly introduced placement path must not silently ignore an accepted override.
			string[] unhandled = Job.MonumentPriority.Keys.Union(Job.MonumentMinWorldSize.Keys)
				.Where(fragment => !Monuments.Keys.Any(prefab => prefab.Contains(fragment))).ToArray();
			if (unhandled.Length > 0)
			{
				string error = "monument overrides did not reach a placement hook: " + string.Join(", ", unhandled);
				File.WriteAllText(Path.Combine(Job.Out, "failed"), error + "\n");
				Log(error);
				Application.Quit();
				return;
			}
			double seconds = (DateTime.UtcNow - Started).TotalSeconds;
			Dictionary<string, object> report = new Dictionary<string, object>
			{
				["file"] = fileName,
				["size"] = World.Size,
				["seed"] = World.Seed,
				["salt"] = World.Salt,
				["seconds_to_save"] = Math.Round(seconds, 1),
				["notes"] = Notes,
			};
			try
			{
				List<Dictionary<string, object>> placed = new List<Dictionary<string, object>>();
				List<MonumentInfo> monuments = AccessTools.Field(typeof(TerrainPath), "Monuments").GetValue(TerrainMeta.Path) as List<MonumentInfo>;
				foreach (MonumentInfo info in monuments ?? new List<MonumentInfo>())
				{
					if (info == null)
					{
						continue;
					}
					Vector3 position = info.transform.position;
					placed.Add(new Dictionary<string, object>
					{
						["object"] = info.name,
						["name"] = info.displayPhrase != null && info.displayPhrase.IsValid() ? info.displayPhrase.english : string.Empty,
						["x"] = Math.Round(position.x, 1),
						["y"] = Math.Round(position.y, 1),
						["z"] = Math.Round(position.z, 1),
						["turn"] = Math.Round(info.transform.rotation.eulerAngles.y, 1),
						["type"] = info.Type.ToString(),
						["tier"] = info.Tier.ToString(),
						["on_map"] = info.shouldDisplayOnMap,
					});
				}
				report["monuments"] = placed;
			}
			catch (Exception error)
			{
				Note("monument list not read: " + error.Message);
			}
			WritePrefabTable();
			report["seconds_total"] = Math.Round((DateTime.UtcNow - Started).TotalSeconds, 1);
			Write("census.json", new Dictionary<string, object> { ["steps"] = Steps, ["monuments"] = Monuments.Values.OrderBy(row => (string)row["prefab"]).ToList() });
			Write("report.json", report);
			File.WriteAllText(Path.Combine(Job.Out, "done"), "saved\n");
			Log("map saved and described: quitting");
			Application.Quit();
		}
	}

	public sealed class Hooks : IHarmonyModHooks
	{
		public void OnLoaded(OnHarmonyModLoadedArgs args)
		{
			// The runner waits for one of these two lines and stops a game that says neither.
			State.Log(State.Active ? "patches active" : "patches idle");
		}

		public void OnUnloaded(OnHarmonyModUnloadedArgs args)
		{
		}
	}

	[HarmonyPatch(typeof(GenerateRailRing), nameof(GenerateRailRing.Process))]
	internal static class RailRing
	{
		private static void Prefix(GenerateRailRing __instance)
		{
			if (!State.Active)
			{
				return;
			}
			State.Steps.Add(new Dictionary<string, object> { ["step"] = "GenerateRailRing", ["game_min_world_size"] = __instance.MinWorldSize, ["world_size"] = World.Size });
			if (State.Job.RingMinWorldSize >= 0)
			{
				__instance.MinWorldSize = State.Job.RingMinWorldSize;
			}
		}
	}

	[HarmonyPatch(typeof(GenerateHeight), nameof(GenerateHeight.Process))]
	internal static class Land
	{
		// The value where `share` of the land (every value above `shore`) lies below, in map units.
		private static int Quantile(int[] histogram, int shore, long land, double share)
		{
			long wanted = (long)(land * share), seen = 0;
			for (int value = shore + 1; value < histogram.Length; value++)
			{
				seen += histogram[value];
				if (seen >= wanted)
				{
					return value;
				}
			}
			return histogram.Length - 1;
		}

		// A box blur along rows then columns, in place: the mean of the samples within `radius`.
		private static void Blur(float[] values, float[] spare, int side, int radius)
		{
			for (int pass = 0; pass < 2; pass++)
			{
				float[] source = pass == 0 ? values : spare, target = pass == 0 ? spare : values;
				int stride = pass == 0 ? 1 : side, line = pass == 0 ? side : 1;
				for (int row = 0; row < side; row++)
				{
					int start = row * line;
					double sum = 0;
					for (int k = -radius; k <= radius; k++)
					{
						sum += source[start + Mathf.Clamp(k, 0, side - 1) * stride];
					}
					for (int at = 0; at < side; at++)
					{
						target[start + at * stride] = (float)(sum / (2 * radius + 1));
						sum += source[start + Mathf.Min(at + radius + 1, side - 1) * stride] - source[start + Mathf.Max(at - radius, 0) * stride];
					}
				}
			}
		}

		private static void Postfix()
		{
			if (!State.Active || State.Job.LoadTest)
			{
				return;
			}
			Job job = State.Job;
			NativeArray<short> data = (NativeArray<short>)AccessTools.Field(typeof(TerrainMap<short>), "dst").GetValue(TerrainMeta.HeightMap);
			float step = TerrainMeta.Size.y / 32766f;       // metres per unit of the height map
			float sea = -TerrainMeta.Position.y;            // height of sea level above the terrain's base
			int[] histogram = new int[32768];
			for (int i = 0; i < data.Length; i++)
			{
				histogram[Mathf.Clamp(data[i], 0, 32767)]++;
			}
			int seaValue = Mathf.RoundToInt(sea / step);
			long before = 0;
			for (int value = seaValue + 1; value < histogram.Length; value++)
			{
				before += histogram[value];
			}

			// How far the shore moves. A wanted share of land is read off the heights the game made:
			// the level above which that share of the square lies becomes sea level.
			float lift = job.CoastLift;
			if (job.LandShare > 0f)
			{
				long wanted = (long)(job.LandShare * (double)data.Length), seen = 0;
				int value = histogram.Length - 1;
				while (value > 0 && seen < wanted)
				{
					seen += histogram[value];
					value--;
				}
				lift = Mathf.Clamp(sea - value * step, -10f, 20f);
			}
			float fade = Mathf.Max(1f, job.CoastFade);

			// How high the land stands. A profile gives the height wanted at five points of the land's
			// own spread (the level 10, 50, 90 and 99 % of it lies below, and its top); the land the
			// game made is bent to it, piece by piece between those points.
			int shore = Mathf.RoundToInt((sea - lift) / step);
			long land = 0;
			int top = shore + 1;
			for (int value = Mathf.Max(0, shore + 1); value < histogram.Length; value++)
			{
				land += histogram[value];
				if (histogram[value] > 0)
				{
					top = value;
				}
			}
			List<float> from = new List<float> { 0f }, to = new List<float> { 0f }, made = new List<float>();
			bool profile = job.HeightProfile != null && job.HeightProfile.Length == 5 && land > 1000;
			if (profile)
			{
				double[] shares = { 0.10, 0.50, 0.90, 0.99 };
				for (int i = 0; i < 5; i++)
				{
					int value = i < 4 ? Quantile(histogram, shore, land, shares[i]) : top;
					float height = Mathf.Max(from[from.Count - 1] + 0.05f, (value - shore) * step);
					made.Add(height);
					from.Add(height);
					to.Add(Mathf.Max(to[to.Count - 1] + 0.01f, job.HeightProfile[i]));
				}
			}
			else
			{
				from.Add(1000f);
				to.Add(1000f * job.HeightScale);
			}
			// With a relief radius the profile is read off, and applied to, the land seen from afar.
			bool relief = job.ReliefRadius > 0.5f && land > 1000 && (profile || !Mathf.Approximately(job.HeightScale, 1f));
			float[] smooth = null;
			if (relief)
			{
				int side = Mathf.RoundToInt(Mathf.Sqrt(data.Length));       // the height map is square
				int radius = Mathf.Clamp(Mathf.RoundToInt(job.ReliefRadius / (TerrainMeta.Size.x / (side - 1))), 1, 600);
				smooth = new float[data.Length];
				for (int i = 0; i < data.Length; i++)
				{
					smooth[i] = Mathf.Max(0f, (data[i] - shore) * step);
				}
				float[] spare = new float[data.Length];
				Blur(smooth, spare, side, radius);
				Blur(smooth, spare, side, radius);
				if (profile)
				{
					int[] spread = new int[32768];
					int highest = 0;
					for (int i = 0; i < data.Length; i++)
					{
						if (data[i] > shore)
						{
							int value = Mathf.Clamp(Mathf.RoundToInt(smooth[i] / step), 0, 32767);
							spread[value]++;
							highest = Mathf.Max(highest, value);
						}
					}
					double[] shares = { 0.10, 0.50, 0.90, 0.99 };
					from = new List<float> { 0f };
					made = new List<float>();
					for (int i = 0; i < 5; i++)
					{
						float height = Mathf.Max(from[from.Count - 1] + 0.05f, (i < 4 ? Quantile(spread, -1, land, shares[i]) : highest) * step);
						made.Add(height);
						from.Add(height);
					}
				}
			}

			// The sea floor off the shore, the same way: the wanted depth at given shares of the square.
			List<float> seaFrom = new List<float> { 0f }, seaTo = new List<float> { 0f };
			bool shallows = job.SeaShares != null && job.SeaDepths != null && job.SeaShares.Length > 0 && job.SeaShares.Length == job.SeaDepths.Length;
			if (shallows)
			{
				long below = 0;
				int at = Mathf.Clamp(shore, 0, histogram.Length - 1);
				for (int i = 0; i < job.SeaShares.Length; i++)
				{
					long wanted = (long)(job.SeaShares[i] * (double)data.Length);
					while (at > 0 && below < wanted)
					{
						below += histogram[at];
						at--;
					}
					seaFrom.Add(Mathf.Max(seaFrom[seaFrom.Count - 1] + 0.05f, (shore - at) * step));
					seaTo.Add(Mathf.Max(seaTo[seaTo.Count - 1] + 0.01f, job.SeaDepths[i]));
				}
				// From the last wanted depth the floor runs back to the depth the game gave it.
				float rejoin = Mathf.Max(seaFrom[seaFrom.Count - 1] + 5f, seaTo[seaTo.Count - 1] + fade);
				seaFrom.Add(rejoin);
				seaTo.Add(rejoin + lift);
			}
			bool change = Mathf.Abs(lift) > 0.001f || profile || shallows || !Mathf.Approximately(job.HeightScale, 1f);
			long after = before;
			if (change)
			{
				// One answer per height the map can hold, then every sample looks its own up.
				short[] table = new short[32768];
				for (int value = 0; value < table.Length; value++)
				{
					float height = value * step - sea, moved;
					if (height >= -lift)
					{
						float above = height + lift;
						int piece = 1;
						while (piece < from.Count - 1 && above > from[piece])
						{
							piece++;
						}
						moved = to[piece - 1] + (above - from[piece - 1]) * (to[piece] - to[piece - 1]) / (from[piece] - from[piece - 1]);
					}
					else if (shallows)
					{
						float under = -lift - height;          // metres under the new shore, as the game made it
						if (under >= seaFrom[seaFrom.Count - 1])
						{
							moved = height;
						}
						else
						{
							int piece = 1;
							while (piece < seaFrom.Count - 1 && under > seaFrom[piece])
							{
								piece++;
							}
							moved = -(seaTo[piece - 1] + (under - seaFrom[piece - 1]) * (seaTo[piece] - seaTo[piece - 1]) / (seaFrom[piece] - seaFrom[piece - 1]));
						}
					}
					else
					{
						moved = height + lift * (1f - Mathf.Clamp01((-lift - height) / fade));
					}
					table[value] = (short)Mathf.Clamp(Mathf.RoundToInt((moved + sea) / step), 0, 32766);
				}
				after = 0;
				for (int i = 0; i < data.Length; i++)
				{
					short value = table[Mathf.Clamp(data[i], 0, 32767)];
					if (relief && data[i] > shore)
					{
						// The bent height of the land from afar, plus what this sample stands out against it.
						float afar = smooth[i];
						int piece = 1;
						while (piece < from.Count - 1 && afar > from[piece])
						{
							piece++;
						}
						float bent = to[piece - 1] + (afar - from[piece - 1]) * (to[piece] - to[piece - 1]) / (from[piece] - from[piece - 1]);
						float moved = Mathf.Max(0.15f, bent + job.ReliefKeep * ((data[i] - shore) * step - afar));
						value = (short)Mathf.Clamp(Mathf.RoundToInt((moved + sea) / step), 0, 32766);
					}
					data[i] = value;
					if (value > seaValue)
					{
						after++;
					}
				}
			}
			float plateau = 0f;
			if (job.MiddlePlateau > 1f)
			{
				int side = Mathf.RoundToInt(Mathf.Sqrt(data.Length));
				int reach = Mathf.Clamp(Mathf.RoundToInt(job.MiddlePlateau / (TerrainMeta.Size.x / (side - 1))), 2, side / 2 - 1);
				int middle = side / 2;
				double sum = 0;
				long count = 0;
				for (int z = middle - reach / 2; z <= middle + reach / 2; z++)
				{
					for (int x = middle - reach / 2; x <= middle + reach / 2; x++)
					{
						sum += data[z * side + x];
						count++;
					}
				}
				// The level of the plateau: the mean of the ground around the middle, and never under 3 m above the sea.
				float level = Mathf.Max((float)(sum / count), (sea + 3f) / step);
				plateau = level * step - sea;
				for (int z = middle - reach; z <= middle + reach; z++)
				{
					for (int x = middle - reach; x <= middle + reach; x++)
					{
						float away = Mathf.Sqrt((x - middle) * (x - middle) + (z - middle) * (z - middle)) / reach;
						if (away >= 1f)
						{
							continue;
						}
						float weight = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01((1f - away) * 2f));        // level over the inner half, eased out over the outer
						int at = z * side + x;
						data[at] = (short)Mathf.Clamp(Mathf.RoundToInt(Mathf.Lerp(data[at], level, weight)), 0, 32766);
					}
				}
			}
			// What moving the shore would make land, read off the game's own heights: the reach of a
			// wanted share of land, which this mod moves by 20 m at the most.
			Dictionary<string, double> byMove = new Dictionary<string, double>();
			foreach (int metres in new[] { 0, 10, 20, 30, 40, 60 })
			{
				long count = 0;
				for (int value = Mathf.Max(0, Mathf.RoundToInt((sea - metres) / step) + 1); value < histogram.Length; value++)
				{
					count += histogram[value];
				}
				byMove[metres.ToString()] = Math.Round((double)count / data.Length, 4);
			}
			State.Steps.Add(new Dictionary<string, object>
			{
				["land_share_by_shore_move_m"] = byMove,
				["step"] = "Land", ["middle_plateau_m"] = job.MiddlePlateau, ["middle_level_m"] = Math.Round(plateau, 1), ["coast_lift_m"] = Math.Round(lift, 2), ["coast_fade_m"] = fade, ["height_scale"] = job.HeightScale,
				["relief_radius_m"] = relief ? job.ReliefRadius : 0f, ["relief_keep"] = job.ReliefKeep,
				["land_share_wanted"] = job.LandShare, ["profile_wanted"] = profile ? job.HeightProfile : null,
				["profile_made"] = made.Select(height => Math.Round(height, 1)).ToList(),
				["shallows_made"] = seaFrom.Skip(1).Select(depth => Math.Round(depth, 1)).ToList(), ["shallows_wanted"] = shallows ? job.SeaDepths : null,
				["game_land_share"] = Math.Round((double)before / data.Length, 4), ["land_share"] = Math.Round((double)after / data.Length, 4),
			});
			State.Log($"land {100.0 * before / data.Length:0.0}% of the square as the game made it, {100.0 * after / data.Length:0.0}% after the recipe (shore moved {lift:0.0} m)");
		}
	}

	[HarmonyPatch(typeof(GenerateRoadRing), nameof(GenerateRoadRing.Process))]
	internal static class RoadRing
	{
		private static void Prefix(GenerateRoadRing __instance)
		{
			if (!State.Active)
			{
				return;
			}
			State.Steps.Add(new Dictionary<string, object> { ["step"] = "GenerateRoadRing", ["game_min_world_size"] = __instance.MinWorldSize, ["world_size"] = World.Size });
			if (State.Job.RoadRingMinWorldSize >= 0)
			{
				__instance.MinWorldSize = State.Job.RoadRingMinWorldSize;
			}
		}
	}

	[HarmonyPatch(typeof(PlaceMonumentsRailside), nameof(PlaceMonumentsRailside.Process))]
	internal static class Railside
	{
		private static void Prefix(PlaceMonumentsRailside __instance)
		{
			if (!State.Active)
			{
				return;
			}
			State.Steps.Add(new Dictionary<string, object>
			{
				["step"] = "PlaceMonumentsRailside", ["description"] = __instance.Description, ["folder"] = __instance.ResourceFolder,
				["game_min_world_size"] = __instance.MinWorldSize, ["game_target_count"] = __instance.TargetCount,
			});
			if (State.Job.RailsideMinWorldSize >= 0)
			{
				__instance.MinWorldSize = State.Job.RailsideMinWorldSize;
			}
			State.Monument("PlaceMonumentsRailside", __instance.ResourceFolder);
		}
	}

	[HarmonyPatch(typeof(PlaceMonumentsRoadside), nameof(PlaceMonumentsRoadside.Process))]
	internal static class Roadside
	{
		private static void Prefix(PlaceMonumentsRoadside __instance)
		{
			if (!State.Active) return;
			State.Steps.Add(new Dictionary<string, object>
			{
				["step"] = "PlaceMonumentsRoadside", ["folder"] = __instance.ResourceFolder,
				["game_min_world_size"] = __instance.MinWorldSize, ["game_target_count"] = __instance.TargetCount,
			});
			State.Monument("PlaceMonumentsRoadside", __instance.ResourceFolder);
			if (State.WantsFolder(__instance.ResourceFolder)) __instance.MinWorldSize = 0;
		}
	}

	[HarmonyPatch(typeof(PlaceMonumentsOffshore), nameof(PlaceMonumentsOffshore.Process))]
	internal static class Offshore
	{
		private static void Prefix(PlaceMonumentsOffshore __instance)
		{
			if (!State.Active) return;
			State.Steps.Add(new Dictionary<string, object>
			{
				["step"] = "PlaceMonumentsOffshore", ["folder"] = __instance.ResourceFolder,
				["game_min_world_size"] = __instance.MinWorldSize, ["game_target_count"] = __instance.TargetCount,
			});
			State.Monument("PlaceMonumentsOffshore", __instance.ResourceFolder);
			if (State.WantsFolder(__instance.ResourceFolder)) __instance.MinWorldSize = 0;
		}
	}

	[HarmonyPatch(typeof(PlaceMonuments), nameof(PlaceMonuments.Process))]
	internal static class Monuments
	{
		private static void Prefix(PlaceMonuments __instance)
		{
			if (!State.Active)
			{
				return;
			}
			Dictionary<string, object> row = new Dictionary<string, object>
			{
				["step"] = "PlaceMonuments", ["description"] = __instance.Description, ["folder"] = __instance.ResourceFolder,
				["game_min_world_size"] = __instance.MinWorldSize, ["game_target_count"] = __instance.TargetCount,
				["size_multiplier"] = Math.Round(__instance.TargetCountWorldSizeMultiplier.Evaluate(World.Size), 3),
				["min_distance_same"] = __instance.MinDistanceSameType, ["min_distance_other"] = __instance.MinDistanceDifferentType,
				["listed"] = __instance.Monuments != null ? __instance.Monuments.Length : 0,
			};
			StepRule rule = State.Rule(__instance.ResourceFolder);
			if (rule != null)
			{
				if (rule.Count >= 0) __instance.TargetCount = rule.Count;
				if (rule.MinWorldSize >= 0) __instance.MinWorldSize = rule.MinWorldSize;
				if (rule.DistanceSame >= 0) __instance.MinDistanceSameType = rule.DistanceSame;
				if (rule.DistanceOther >= 0) __instance.MinDistanceDifferentType = rule.DistanceOther;
			}
			float relax = State.Job.RelaxDistances;
			if (relax > 0f && !Mathf.Approximately(relax, 1f))
			{
				__instance.MinDistanceSameType = Mathf.RoundToInt(__instance.MinDistanceSameType * relax);
				__instance.MinDistanceDifferentType = Mathf.RoundToInt(__instance.MinDistanceDifferentType * relax);
			}
			row["target_count"] = __instance.TargetCount;
			row["min_world_size"] = __instance.MinWorldSize;
			row["distance_same"] = __instance.MinDistanceSameType;
			row["distance_other"] = __instance.MinDistanceDifferentType;
			State.Steps.Add(row);
			State.Monument("PlaceMonuments", __instance.ResourceFolder, __instance.Filter);
		}
	}

	[HarmonyPatch(typeof(GenerateBiome), nameof(GenerateBiome.Process))]
	internal static class Biomes
	{
		private static void Prefix()
		{
			if (!State.Active || State.Job.LoadTest || State.Job.BiomeJungle < 0f)
			{
				return;
			}
			float game = World.Config.PercentageBiomeJungle;
			World.Config.PercentageBiomeJungle = Mathf.Clamp01(State.Job.BiomeJungle);
			State.Log($"jungle: {World.Config.PercentageBiomeJungle:0.##} of tier 0 (the game had {game:0.##}: its world config file does not carry this share)");
		}
	}

	// The monument a recipe wants in the middle of the map. The game draws candidate places at
	// random over the whole map and keeps the best; for that one monument the draws are pulled
	// towards the middle, close at first and wider with every attempt.
	public static class Centre
	{
		public static bool On;
		public static int Draws;

		public static float Range(ref uint seed, float min, float max)
		{
			float value = SeedRandom.Range(ref seed, min, max);
			if (!On || Mathf.Abs(max - min - TerrainMeta.Size.x) > 0.5f)
			{
				return value;       // not a draw of a place on the map
			}
			float half = (max - min) * 0.5f;
			float reach = Mathf.Min(half, State.Job.CentreStart + (Draws / 2) * State.Job.CentreGrow);
			Draws++;
			float middle = (min + max) * 0.5f;
			return middle + (value - middle) / half * reach;
		}
	}

	[HarmonyPatch(typeof(PlaceMonuments), "TryFindSpawn")]
	internal static class CentreSearch
	{
		private static void Prefix(Prefab<MonumentInfo> prefab)
		{
			Centre.On = State.Active && !State.Job.LoadTest && State.Job.CentreMonument.Length > 0 && prefab.Name.Contains(State.Job.CentreMonument);
			Centre.Draws = 0;
		}

		private static void Finalizer()
		{
			Centre.On = false;
		}

		private static IEnumerable<CodeInstruction> Transpiler(IEnumerable<CodeInstruction> instructions)
		{
			MethodInfo game = AccessTools.Method(typeof(SeedRandom), nameof(SeedRandom.Range), new[] { typeof(uint).MakeByRefType(), typeof(float), typeof(float) });
			MethodInfo ours = AccessTools.Method(typeof(Centre), nameof(Centre.Range));
			int swapped = 0;
			foreach (CodeInstruction instruction in instructions)
			{
				if (instruction.opcode == OpCodes.Call && Equals(instruction.operand, game))
				{
					instruction.operand = ours;
					swapped++;
				}
				yield return instruction;
			}
			State.Log("monument search: " + swapped + " random draws routed through the recipe");
		}
	}

	[HarmonyPatch(typeof(WorldSerialization), nameof(WorldSerialization.Save))]
	internal static class Saved
	{
		private static void Postfix(string fileName)
		{
			if (State.Active && !State.Job.LoadTest)
			{
				State.Finish(fileName);
			}
		}
	}

	// A server that starts on a generated map sends that map and its picture to Facepunch's map
	// cache (api.facepunch.com), so that players can fetch it. A generation quits before that step;
	// these two make sure no run of the generator ever gets there.
	[HarmonyPatch(typeof(MapUploader), nameof(MapUploader.UploadMap))]
	internal static class NoMapUpload
	{
		private static bool Prefix(ref Task __result)
		{
			State.Log("map upload to the game's map cache skipped");
			__result = Task.CompletedTask;
			return false;
		}
	}

	[HarmonyPatch(typeof(MapUploader), nameof(MapUploader.UploadMapImage))]
	internal static class NoMapImageUpload
	{
		private static bool Prefix()
		{
			return false;
		}
	}

	// The Rust+ companion server is started right after the network step: it stays off as well.
	[HarmonyPatch(typeof(CompanionServer.Server), nameof(CompanionServer.Server.Initialize))]
	internal static class NoCompanion
	{
		private static bool Prefix()
		{
			return false;
		}
	}

	// A generator has no business on the network: stop before the server listens or tells Steam.
	[HarmonyPatch(typeof(ServerMgr), nameof(ServerMgr.OpenConnection))]
	internal static class NoNetwork
	{
		private static bool Prefix()
		{
			State.Log("the map generator never opens a connection: quitting");
			if (State.Active && State.Job.LoadTest)
			{
				State.Loaded();
			}
			else if (State.Active)
			{
				File.WriteAllText(Path.Combine(State.Job.Out, "failed"), "reached the network step without saving a map (was the map already in the identity folder?)\n");
			}
			Application.Quit();
			return false;
		}
	}
}
