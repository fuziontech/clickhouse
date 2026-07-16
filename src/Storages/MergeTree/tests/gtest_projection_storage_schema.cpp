#include <gtest/gtest.h>

#include <Disks/DiskLocal.h>
#include <Disks/SingleDiskVolume.h>
#include <Storages/MergeTree/DataPartStorageOnDiskFull.h>

#include <filesystem>

using namespace DB;

namespace
{

struct PartStorageFixture
{
    std::filesystem::path base_path;
    std::string part_dir;
    DiskPtr disk;
    VolumePtr volume;
    std::shared_ptr<DataPartStorageOnDiskFull> storage;

    PartStorageFixture()
    {
        auto base = std::filesystem::temp_directory_path();
        auto unique_id = std::to_string(::getpid()) + "_" + std::to_string(reinterpret_cast<uintptr_t>(this));
        base_path = base / ("projection_schema_gtest_" + unique_id);
        std::filesystem::create_directories(base_path);
        part_dir = "all_1_1_0";
        std::filesystem::create_directories(base_path / part_dir);

        disk = std::make_shared<DiskLocal>("test_disk_" + unique_id, base_path.string());
        volume = std::make_shared<SingleDiskVolume>("test_volume", disk);
        storage = std::make_shared<DataPartStorageOnDiskFull>(volume, /*root_path=*/ "", part_dir);
    }

    ~PartStorageFixture()
    {
        std::error_code ec;
        std::filesystem::remove_all(base_path, ec);
    }
};

}

TEST(ProjectionDirNames, Classification)
{
    EXPECT_EQ(projectionDirNameType("p.proj"), ProjectionDirNameType::Normal);
    EXPECT_EQ(projectionDirNameType("p.tmp_proj"), ProjectionDirNameType::Temp);
    EXPECT_EQ(projectionDirNameType("all_1_1_0.p.proj"), ProjectionDirNameType::Normal);
    EXPECT_EQ(projectionDirNameType("all_1_1_0.p_1.tmp_proj"), ProjectionDirNameType::Temp);
    EXPECT_EQ(projectionDirNameType("all_1_1_0"), ProjectionDirNameType::None);
    EXPECT_EQ(projectionDirNameType("proj"), ProjectionDirNameType::None);
    EXPECT_EQ(projectionDirNameType(""), ProjectionDirNameType::None);
}

TEST(ProjectionDirNames, SiblingOwner)
{
    EXPECT_EQ(projectionSiblingOwner("all_1_1_0.p.proj"), "all_1_1_0");
    EXPECT_EQ(projectionSiblingOwner("all_1_1_0.p.tmp_proj"), "all_1_1_0");
    /// Projection names may contain dots; the owner is everything before the first one.
    EXPECT_EQ(projectionSiblingOwner("all_1_1_0.my.p.proj"), "all_1_1_0");
    /// A nested dir name has no owner prefix.
    EXPECT_EQ(projectionSiblingOwner("p.proj"), "");
    EXPECT_EQ(projectionSiblingOwner("p.tmp_proj"), "");
    EXPECT_EQ(projectionSiblingOwner("all_1_1_0"), "");
    /// Owner equality distinguishes "part_1" from "part_10".
    EXPECT_EQ(projectionSiblingOwner("part_10.p.proj"), "part_10");
    EXPECT_NE(projectionSiblingOwner("part_10.p.proj"), "part_1");
}

TEST(ProjectionStorageSchema, UnseededReadsThrow)
{
    PartStorageFixture fixture;
    EXPECT_THROW(fixture.storage->getProjections(), Exception);
    EXPECT_THROW(fixture.storage->getProjection("p.proj", true), Exception);
    EXPECT_THROW(fixture.storage->renameProjection("p.proj", "q.proj"), Exception);
    EXPECT_THROW(fixture.storage->removeTempProjection("p.tmp_proj"), Exception);
}

TEST(ProjectionStorageSchema, EmptySchemaIsValid)
{
    PartStorageFixture fixture;
    fixture.storage->setProjections({});
    EXPECT_TRUE(fixture.storage->getProjections().empty());
    EXPECT_FALSE(fixture.storage->hasProjection("p.proj"));
}

TEST(ProjectionStorageSchema, CreateRenameRemoveMaintainCache)
{
    PartStorageFixture fixture;
    fixture.storage->setProjectionStorageFormat(IDataPartStorage::ProjectionStorageFormat::FLAT);
    fixture.storage->setProjections({});

    fixture.storage->createProjection("p_1.tmp_proj");
    ASSERT_TRUE(fixture.storage->hasProjection("p_1.tmp_proj"));
    ASSERT_TRUE(std::filesystem::exists(fixture.base_path / "all_1_1_0.p_1.tmp_proj"));

    fixture.storage->renameProjection("p_1.tmp_proj", "p.proj");
    EXPECT_FALSE(fixture.storage->hasProjection("p_1.tmp_proj"));
    ASSERT_TRUE(fixture.storage->hasProjection("p.proj"));
    EXPECT_FALSE(std::filesystem::exists(fixture.base_path / "all_1_1_0.p_1.tmp_proj"));
    ASSERT_TRUE(std::filesystem::exists(fixture.base_path / "all_1_1_0.p.proj"));

    /// Only temporary projections may be removed individually.
    EXPECT_THROW(fixture.storage->removeTempProjection("p.proj"), Exception);

    fixture.storage->createProjection("q.tmp_proj");
    fixture.storage->removeTempProjection("q.tmp_proj");
    EXPECT_FALSE(fixture.storage->hasProjection("q.tmp_proj"));
    EXPECT_FALSE(std::filesystem::exists(fixture.base_path / "all_1_1_0.q.tmp_proj"));

    /// The schema survives and matches disk truth.
    auto detected = fixture.storage->detectProjections();
    ASSERT_EQ(detected.size(), 1u);
    EXPECT_TRUE(detected.contains("p.proj"));
    EXPECT_EQ(detected.at("p.proj").format, IDataPartStorage::ProjectionStorageFormat::FLAT);
}

TEST(ProjectionStorageSchema, DetectProjectionsBothLayouts)
{
    PartStorageFixture fixture;
    std::filesystem::create_directories(fixture.base_path / fixture.part_dir / "nested.proj");
    std::filesystem::create_directories(fixture.base_path / "all_1_1_0.flat.proj");
    std::filesystem::create_directories(fixture.base_path / "all_1_1_0.tmp.tmp_proj");
    std::filesystem::create_directories(fixture.base_path / "all_1_1_1.other.proj");   /// different owner

    auto detected = fixture.storage->detectProjections();
    ASSERT_EQ(detected.size(), 3u);
    EXPECT_EQ(detected.at("nested.proj").format, IDataPartStorage::ProjectionStorageFormat::LEGACY_NESTED);
    EXPECT_EQ(detected.at("flat.proj").format, IDataPartStorage::ProjectionStorageFormat::FLAT);
    EXPECT_TRUE(detected.at("tmp.tmp_proj").is_temp);
}
