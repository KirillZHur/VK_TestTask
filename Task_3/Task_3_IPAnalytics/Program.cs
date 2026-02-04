using System;
using System.Net;
using Newtonsoft.Json;

public class Program
{
	private const string IpFileName = "IPs.txt";

	private const int MaxConcurrency = 4;

	// Если понадобится токен ipinfo (для повышения лимитов), можно задать переменной окружения:
	// set IPINFO_TOKEN=xxxx  (Windows)
	// export IPINFO_TOKEN=xxxx (macOS/Linux)
	private static readonly string? IpInfoToken = Environment.GetEnvironmentVariable("IPINFO_TOKEN");
	public static async Task Main(string[] args)
	{
		try
		{
			var ips = LoadIps(IpFileName);

			Console.WriteLine($"Загружено IP адресов: {ips.Count}");
			if (ips.Count == 0)
			{
				Console.WriteLine("Файл пустой.");
				return;
			}


			var ipDataList = await FetchAllIpDataAsync(ips);

			// Отфильтруем записи, где не удалось получить страну
			var valid = ipDataList.Where(x => !string.IsNullOrWhiteSpace(x.Country)).ToList();

			Console.WriteLine();
			Console.WriteLine($"Успешно получены данные: {valid.Count} из {ips.Count}");

			if (valid.Count == 0)
			{
				Console.WriteLine("Не удалось получить данные ipinfo для всех IP.");
				return;
			}

			var byCountry = valid
				.GroupBy(x => x.Country!)
				.Select(g => new { Country = g.Key, Count = g.Count() })
				.OrderByDescending(x => x.Count)
				.ThenBy(x => x.Country)
				.ToList();

			Console.WriteLine();
			Console.WriteLine("Страны и количество IP:");
			foreach (var item in byCountry)
			{
				Console.WriteLine($"{item.Country} - {item.Count}");
			}

			var top = byCountry.First();
			Console.WriteLine();
			Console.WriteLine($"Страна с максимальным числом IP: {top.Country} ({top.Count})");
			Console.WriteLine("Города для этой страны:");

			var cities = valid
				.Where(x => x.Country == top.Country)
				.Select(x => x.City)
				.Where(c => !string.IsNullOrWhiteSpace(c))
				.Distinct(StringComparer.OrdinalIgnoreCase)
				.ToList();

			if (cities.Count == 0)
			{
				Console.WriteLine("Города не определены ipinfo для этих IP");
			}
			else
			{
				foreach (var city in cities)
					Console.WriteLine(city);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine($"Ошибка выполнения: {ex.Message}");
		}
	}

	/// <summary>
	/// Функция, которая читает IP-адреса из файла, валидирует, удаляет дубликаты.
	/// </summary>
	private static List<string> LoadIps(string fileName)
	{
		var path = Path.Combine(AppContext.BaseDirectory, fileName);

		if (!File.Exists(path))
			throw new FileNotFoundException($"Файл {fileName} не найден рядом с исполняемым файлом: {path}");

		var ips = new List<string>();
		foreach (var raw in File.ReadAllLines(path))
		{
			var ip = raw.Trim();
			if (string.IsNullOrWhiteSpace(ip))
				continue;

			if (IPAddress.TryParse(ip, out _))
				ips.Add(ip);
			else
				Console.WriteLine($"Некорректный IP пропущен: '{ip}'");
		}

		return ips
			.Distinct(StringComparer.OrdinalIgnoreCase)
			.ToList();
	}

	/// <summary>
	/// Функция, которая загружает IpData для всех IP с ограничением параллельности.
	/// </summary>
	private static async Task<List<IpData>> FetchAllIpDataAsync(List<string> ips)
	{
		using var http = new HttpClient
		{
			Timeout = TimeSpan.FromSeconds(10)
		};

		// Ограничиваем параллельность, чтобы не словить в rate limit
		using var throttler = new SemaphoreSlim(MaxConcurrency);

		var tasks = ips.Select(async ip =>
		{
			await throttler.WaitAsync();
			try
			{
				return await FetchIpDataAsync(http, ip);
			}
			finally
			{
				throttler.Release();
			}
		});

		var results = await Task.WhenAll(tasks);
		return results.ToList();
	}

	/// <summary>
	/// Функция, которая ходит в https://ipinfo.io/{ip}/json (GET) и возвращает IpData.
	/// </summary>
	private static async Task<IpData> FetchIpDataAsync(HttpClient http, string ip)
	{
		var url = $"https://ipinfo.io/{ip}/json";
		if (!string.IsNullOrWhiteSpace(IpInfoToken))
		{
			// ipinfo поддерживает token как query параметр
			url += $"?token={Uri.EscapeDataString(IpInfoToken)}";
		}

		try
		{
			var json = await http.GetStringAsync(url);

			var dto = JsonConvert.DeserializeObject<IpInfoResponse>(json);

			return new IpData
			{
				Ip = ip,
				Country = dto?.Country,
				City = dto?.City
			};
		}
		catch (Exception ex)
		{
			Console.WriteLine($"Ошибка запроса для {ip}: {ex.Message}");
			return new IpData
			{
				Ip = ip,
				Country = null,
				City = null
			};
		}
	}
}

/// <summary>
/// Итоговый объект Ip.
/// </summary>
public class IpData
{
	public string Ip { get; set; } = "";
	public string? Country { get; set; }
	public string? City { get; set; }
}

/// <summary>
/// Модель ответа ipinfo (берём только нужные поля).
/// </summary>
public class IpInfoResponse
{
	[JsonProperty("country")]
	public string? Country { get; set; }

	[JsonProperty("city")]
	public string? City { get; set; }
}
